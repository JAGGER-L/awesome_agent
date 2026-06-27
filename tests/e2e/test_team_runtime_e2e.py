from __future__ import annotations

import os
from collections import deque
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from awesome_agent.agents.profiles import RoleModelResolver
from awesome_agent.domain.enums import AgentKind, RunIntent, RunMode, RunStatus
from awesome_agent.domain.models import Repository
from awesome_agent.modeling import (
    AssistantMessage,
    ModelRequest,
    ModelStreamEvent,
    ModelTurn,
    StopReason,
    StructuredModelProvider,
    TurnCompleted,
)
from awesome_agent.observability.repository import PostgresObservabilityRepository
from awesome_agent.persistence.checkpoints import checkpoint_saver
from awesome_agent.persistence.database import create_engine, create_session_factory
from awesome_agent.persistence.dispatch import PostgresRunDispatcher
from awesome_agent.persistence.intake_reservations import (
    PostgresIntakeReservationStore,
)
from awesome_agent.persistence.repository_registry import PostgresRepositoryRegistry
from awesome_agent.persistence.runtime_repository import PostgresRuntimeRepository
from awesome_agent.persistence.tool_invocations import PostgresToolInvocationRepository
from awesome_agent.persistence.validation import PostgresValidationRepository
from awesome_agent.repositories.git import require_primary_clean_repository
from awesome_agent.repositories.worktrees import ManagedRunWorktreeManager
from awesome_agent.runtime.events import EventStream
from awesome_agent.runtime.graphs import TEAM_CODING_GRAPH
from awesome_agent.runtime.intake import RunIntakeService
from awesome_agent.runtime.probe_graph import RuntimeProbeGraph
from awesome_agent.runtime.team_graph import TeamCodingGraph
from awesome_agent.runtime.worker import DurableWorker, WorkerConfig
from awesome_agent.sandbox.process import run_process

pytestmark = pytest.mark.e2e


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
async def test_team_run_executes_through_worker_with_verifier_rework(
    tmp_path: Path,
) -> None:
    repository_path = tmp_path / "repository"
    repository_path.mkdir()
    await _git(repository_path, "init")
    await _git(repository_path, "config", "user.email", "test@example.com")
    await _git(repository_path, "config", "user.name", "Test")
    (repository_path / "README.md").write_text("fixture\n", encoding="utf-8")
    await _git(repository_path, "add", "README.md")
    await _git(repository_path, "commit", "-m", "Initial")
    snapshot = await require_primary_clean_repository(repository_path)

    engine = create_engine(os.environ["AWESOME_AGENT_TEST_DATABASE_URL"])
    sessions = create_session_factory(engine)
    registry = PostgresRepositoryRegistry(sessions)
    registered = await registry.upsert(
        Repository(
            root=snapshot.root,
            display_name="team-fixture",
            git_common_dir=snapshot.git_common_dir,
            default_branch=snapshot.branch,
        )
    )
    runtime = PostgresRuntimeRepository(sessions)
    validation_repository = PostgresValidationRepository(sessions)
    tool_repository = PostgresToolInvocationRepository(sessions)
    observability = PostgresObservabilityRepository(sessions)
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
        goal="Implement backend and verify it",
        intent=RunIntent.MODIFYING,
        mode=RunMode.TEAM,
    )
    provider = SequenceProvider([_turn() for _ in range(7)])

    async with checkpoint_saver(
        os.environ["AWESOME_AGENT_TEST_CHECKPOINT_DATABASE_URL"]
    ) as saver:
        await saver.setup()
        worker = DurableWorker(
            dispatcher=PostgresRunDispatcher(sessions),
            repository=runtime,
            probe_graph=RuntimeProbeGraph(saver),
            team_graph=TeamCodingGraph(
                saver,
                model_resolver=_models(),
                provider_resolver=lambda _: provider,
                validation_repository=validation_repository,
                tool_repository=tool_repository,
                verification_outcomes=["failed", "passed"],
            ),
            config=_worker_config(),
            observability_repository=observability,
        )
        assert await worker.run_once()

    restored = await runtime.get_run(run.id)
    agents = await runtime.list_agents(run.id)
    todos = await runtime.list_todos(run.id)
    events = await runtime.list_events(run.id)
    invocations = await tool_repository.list_for_run(run.id)
    validation = await validation_repository.list_for_run(run.id)
    spans = await observability.list_spans_for_run(run.id)
    model_calls = await observability.list_model_calls_for_run(run.id)
    workspace = Path(restored.workspace_path or "")

    assert restored.status is RunStatus.COMPLETED
    assert restored.graph_name == TEAM_CODING_GRAPH
    assert [agent.kind for agent in agents].count(AgentKind.LEADER) == 1
    assert [agent.kind for agent in agents].count(AgentKind.TEAMMATE) == 2
    assert [agent.kind for agent in agents].count(AgentKind.VERIFIER) == 1
    assert [agent.kind for agent in agents].count(AgentKind.SUBAGENT) == 1
    backend = next(agent for agent in agents if agent.profile == "backend-engineer")
    subagent = next(agent for agent in agents if agent.kind is AgentKind.SUBAGENT)
    assert subagent.parent_agent_id == backend.id
    assert todos[0].status.value == "done"
    assert "team runtime update" in (workspace / "README.md").read_text(
        encoding="utf-8"
    )
    assert "team runtime rework" in (workspace / "README.md").read_text(
        encoding="utf-8"
    )
    assert [item.report.status for item in validation] == ["failed", "passed"]
    assert [item.tool_name for item in invocations] == [
        "repo.status",
        "repo.read",
        "repo.apply_patch",
        "repo.diff",
        "repo.apply_patch",
    ]
    assert {event.event_type.value for event in events} >= {
        "agent.created",
        "todo.created",
        "todo.status_changed",
        "model_call.created",
        "tool_call.created",
        "verification.created",
    }
    assert {span.name for span in spans} >= {
        "run.execute",
        "graph.execute",
        "model.call",
        "tool.call",
    }
    assert len(model_calls) >= 7
    assert {call.agent_id for call in model_calls if call.agent_id is not None} >= {
        agent.id for agent in agents
    }
    assert len(provider.requests) == 7
    await engine.dispose()


def _models() -> RoleModelResolver:
    return RoleModelResolver(
        leader_model="fake-model",
        teammate_model="fake-model",
        verifier_model="fake-model",
        subagent_model="fake-model",
    )


def _turn() -> ModelTurn:
    return ModelTurn(
        assistant=AssistantMessage(content="ack"),
        stop_reason=StopReason.COMPLETED,
        model="fake-model",
        provider="fake",
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
