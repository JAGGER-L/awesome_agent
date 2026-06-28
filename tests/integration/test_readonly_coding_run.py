from __future__ import annotations

import os
from collections import deque
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from awesome_agent.agents.profiles import RoleModelResolver
from awesome_agent.artifacts.store import LocalArtifactStore
from awesome_agent.domain.enums import (
    DispatchStatus,
    EventType,
    RunIntent,
    RunStatus,
    TodoStatus,
)
from awesome_agent.domain.models import Repository
from awesome_agent.modeling import (
    AssistantMessage,
    ModelRequest,
    ModelStreamEvent,
    ModelTurn,
    StopReason,
    StructuredModelProvider,
    ToolCall,
    TurnCompleted,
)
from awesome_agent.persistence.artifacts import PostgresArtifactMetadataRepository
from awesome_agent.persistence.budget import PostgresBudgetRepository
from awesome_agent.persistence.checkpoints import checkpoint_saver
from awesome_agent.persistence.database import create_engine, create_session_factory
from awesome_agent.persistence.dispatch import PostgresRunDispatcher
from awesome_agent.persistence.intake_reservations import (
    PostgresIntakeReservationStore,
)
from awesome_agent.persistence.repository_registry import PostgresRepositoryRegistry
from awesome_agent.persistence.runtime_repository import PostgresRuntimeRepository
from awesome_agent.repositories.git import require_primary_clean_repository
from awesome_agent.repositories.worktrees import ManagedRunWorktreeManager
from awesome_agent.runtime.budget import BudgetPolicy
from awesome_agent.runtime.context import ContextManager, DeterministicSummaryProvider
from awesome_agent.runtime.events import EventStream
from awesome_agent.runtime.intake import RunIntakeService
from awesome_agent.runtime.probe_graph import RuntimeProbeGraph
from awesome_agent.runtime.readonly_graph import ReadOnlyCodingGraph
from awesome_agent.runtime.worker import DurableWorker, WorkerConfig
from awesome_agent.sandbox.process import run_process

pytestmark = pytest.mark.integration


class SequenceProvider(StructuredModelProvider):
    def __init__(self, turns: list[ModelTurn]) -> None:
        self.turns = deque(turns)
        self.requests: list[ModelRequest] = []

    async def stream(
        self,
        request: ModelRequest,
    ) -> AsyncIterator[ModelStreamEvent]:
        self.requests.append(request)
        yield TurnCompleted(turn=self.turns.popleft())


@pytest.mark.skipif(
    "AWESOME_AGENT_TEST_DATABASE_URL" not in os.environ
    or "AWESOME_AGENT_TEST_CHECKPOINT_DATABASE_URL" not in os.environ,
    reason="Runtime and checkpoint databases are not configured.",
)
async def test_fake_provider_completes_read_only_coding_run(
    tmp_path: Path,
) -> None:
    repository_path = tmp_path / "repository"
    repository_path.mkdir()
    await _git(repository_path, "init")
    await _git(repository_path, "config", "user.email", "test@example.com")
    await _git(repository_path, "config", "user.name", "Test")
    (repository_path / "README.md").write_text(
        "# Fixture\nThe parser lives in src/parser.py.\n" + ("long evidence\n" * 2000),
        encoding="utf-8",
    )
    await _git(repository_path, "add", "README.md")
    await _git(repository_path, "commit", "-m", "Initial")
    snapshot = await require_primary_clean_repository(repository_path)

    engine = create_engine(os.environ["AWESOME_AGENT_TEST_DATABASE_URL"])
    sessions = create_session_factory(engine)
    registry = PostgresRepositoryRegistry(sessions)
    registered = await registry.upsert(
        Repository(
            root=snapshot.root,
            display_name="fixture",
            git_common_dir=snapshot.git_common_dir,
            default_branch=snapshot.branch,
        )
    )
    runtime = PostgresRuntimeRepository(sessions)
    budget_repository = PostgresBudgetRepository(sessions)
    artifact_repository = PostgresArtifactMetadataRepository(sessions)
    intake = RunIntakeService(
        registry=registry,
        reservations=PostgresIntakeReservationStore(sessions),
        runtime=runtime,
        events=EventStream(),
        worktrees=ManagedRunWorktreeManager(tmp_path / "worktrees"),
        allowed_roots=[tmp_path],
        model_resolver=_models(),
    )
    run = await intake.create_run(
        repository_id=registered.id,
        goal="Where is the parser documented?",
        intent=RunIntent.READ_ONLY,
    )
    provider = SequenceProvider(
        [
            ModelTurn(
                assistant=AssistantMessage(
                    tool_calls=[
                        ToolCall(
                            call_id="read-readme",
                            name="repo.read",
                            arguments_json='{"path":"README.md"}',
                        )
                    ]
                ),
                stop_reason=StopReason.TOOL_CALLS,
                model="fake-model",
                provider="fake",
            ),
            ModelTurn(
                assistant=AssistantMessage(
                    content="README.md:2 documents the parser at src/parser.py."
                ),
                stop_reason=StopReason.COMPLETED,
                model="fake-model",
                provider="fake",
            ),
        ]
    )
    faulted = False

    async def fail_once(node: str, _: object) -> None:
        nonlocal faulted
        if node == "execute_tools" and not faulted:
            faulted = True
            raise RuntimeError("deterministic post-tool fault")

    async with checkpoint_saver(
        os.environ["AWESOME_AGENT_TEST_CHECKPOINT_DATABASE_URL"]
    ) as saver:
        await saver.setup()
        worker = DurableWorker(
            dispatcher=PostgresRunDispatcher(sessions),
            repository=runtime,
            probe_graph=RuntimeProbeGraph(saver),
            coding_graph=ReadOnlyCodingGraph(
                saver,
                provider_resolver=lambda _: provider,
                fault_hook=fail_once,
                context_manager=ContextManager(
                    summary_provider=DeterministicSummaryProvider(),
                    artifact_store=LocalArtifactStore(tmp_path / "artifacts"),
                    artifact_repository=artifact_repository,
                ),
                budget_repository=budget_repository,
                budget_policy=BudgetPolicy(
                    soft_context_tokens=100,
                    hard_context_tokens=10_000,
                    recent_context_tokens=80,
                    max_total_tokens_per_run=500_000,
                    max_reasoning_tokens_per_run=250_000,
                    max_active_seconds_per_run=3600,
                ),
            ),
            config=_worker_config(),
            budget_repository=budget_repository,
        )
        assert await worker.run_once()
        assert await worker.run_once()

    restored = await runtime.get_run(run.id)
    todos = await runtime.list_todos(run.id)
    events = await runtime.list_events(run.id)
    ledger = await budget_repository.get_ledger(run.id)
    compactions = await budget_repository.list_compactions(run.id)
    assert restored.status is RunStatus.COMPLETED
    assert restored.dispatch_status is DispatchStatus.TERMINAL
    assert restored.result_text == (
        "README.md:2 documents the parser at src/parser.py."
    )
    assert todos[0].status is TodoStatus.DONE
    assert (
        sum(event.event_type is EventType.MODEL_CALL_CREATED for event in events) == 2
    )
    assert sum(event.event_type is EventType.TOOL_CALL_CREATED for event in events) == 1
    assert (
        sum(event.event_type is EventType.DISPATCH_RETRY_SCHEDULED for event in events)
        == 1
    )
    assert ledger.model_call_count == 2
    assert compactions
    assert compactions[0].artifact_refs
    assert len(provider.requests) == 2
    assert all(
        "long evidence\n" * 20 not in getattr(message, "content", "")
        for message in provider.requests[1].messages
    )
    transition_ids = [
        event.transition_id for event in events if event.transition_id is not None
    ]
    assert len(transition_ids) == len(set(transition_ids))
    await engine.dispose()


def _models() -> RoleModelResolver:
    return RoleModelResolver(
        leader_model="fake-model",
        teammate_model="fake-model",
        verifier_model="fake-model",
        subagent_model="fake-model",
    )


def _worker_config() -> WorkerConfig:
    from datetime import timedelta

    return WorkerConfig(
        lease_duration=timedelta(seconds=60),
        heartbeat_interval=timedelta(seconds=15),
        poll_interval=0.01,
        recovery_interval=15,
        shutdown_grace=1,
        retry_delay=timedelta(seconds=0),
        max_attempts=3,
    )


async def _git(path: Path, *arguments: str) -> None:
    result = await run_process(
        ["git", *arguments],
        command_label="git fixture",
        workspace=path,
        timeout_seconds=30,
    )
    assert result.exit_code == 0, result.stderr
