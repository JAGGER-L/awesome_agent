from __future__ import annotations

import json
import os
from collections import deque
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from awesome_agent.agents.profiles import RoleModelResolver
from awesome_agent.artifacts.store import LocalArtifactStore
from awesome_agent.domain.enums import DispatchStatus, RunIntent, RunStatus, TodoStatus
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
from awesome_agent.persistence.checkpoints import checkpoint_saver
from awesome_agent.persistence.database import create_engine, create_session_factory
from awesome_agent.persistence.dispatch import PostgresRunDispatcher
from awesome_agent.persistence.intake_reservations import (
    PostgresIntakeReservationStore,
)
from awesome_agent.persistence.repository_registry import PostgresRepositoryRegistry
from awesome_agent.persistence.runtime_repository import PostgresRuntimeRepository
from awesome_agent.persistence.tool_invocations import PostgresToolInvocationRepository
from awesome_agent.repositories.git import require_primary_clean_repository
from awesome_agent.repositories.worktrees import ManagedRunWorktreeManager
from awesome_agent.runtime.events import EventStream
from awesome_agent.runtime.graphs import MODIFYING_CODING_GRAPH
from awesome_agent.runtime.intake import RunIntakeService
from awesome_agent.runtime.modifying_graph import ModifyingCodingGraph
from awesome_agent.runtime.probe_graph import RuntimeProbeGraph
from awesome_agent.runtime.worker import DurableWorker, WorkerConfig
from awesome_agent.sandbox.process import run_process

pytestmark = pytest.mark.integration


class SequenceProvider(StructuredModelProvider):
    def __init__(self, turns: list[ModelTurn]) -> None:
        self.turns = deque(turns)

    async def stream(
        self,
        request: ModelRequest,
    ) -> AsyncIterator[ModelStreamEvent]:
        yield TurnCompleted(turn=self.turns.popleft())


@pytest.mark.skipif(
    "AWESOME_AGENT_TEST_DATABASE_URL" not in os.environ
    or "AWESOME_AGENT_TEST_CHECKPOINT_DATABASE_URL" not in os.environ,
    reason="Runtime and checkpoint databases are not configured.",
)
async def test_modifying_run_persists_tool_invocations_across_retry(
    tmp_path: Path,
) -> None:
    repository_path = tmp_path / "repository"
    repository_path.mkdir()
    await _git(repository_path, "init")
    await _git(repository_path, "config", "user.email", "test@example.com")
    await _git(repository_path, "config", "user.name", "Test")
    (repository_path / "README.md").write_text("old\n", encoding="utf-8")
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
        goal="Change README from old to new.",
        intent=RunIntent.MODIFYING,
    )
    patch = """diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -1 +1 @@
-old
+new
"""
    provider = SequenceProvider(
        [
            ModelTurn(
                assistant=AssistantMessage(
                    tool_calls=[
                        ToolCall(
                            call_id="shell-probe",
                            name="shell.execute",
                            arguments_json=(
                                '{"argv":["pytest","--version"],"timeout_seconds":10}'
                            ),
                        )
                    ]
                ),
                stop_reason=StopReason.TOOL_CALLS,
                model="fake-model",
                provider="fake",
            ),
            ModelTurn(
                assistant=AssistantMessage(
                    tool_calls=[
                        ToolCall(
                            call_id="patch-readme",
                            name="repo.apply_patch",
                            arguments_json=json.dumps({"patch": patch}),
                        )
                    ]
                ),
                stop_reason=StopReason.TOOL_CALLS,
                model="fake-model",
                provider="fake",
            ),
            ModelTurn(
                assistant=AssistantMessage(
                    tool_calls=[
                        ToolCall(
                            call_id="final-diff",
                            name="repo.diff",
                            arguments_json="{}",
                        )
                    ]
                ),
                stop_reason=StopReason.TOOL_CALLS,
                model="fake-model",
                provider="fake",
            ),
            ModelTurn(
                assistant=AssistantMessage(
                    content="Changed README.md. Validation has not been run."
                ),
                stop_reason=StopReason.COMPLETED,
                model="fake-model",
                provider="fake",
            ),
        ]
    )
    tool_repository = PostgresToolInvocationRepository(sessions)
    faulted = False

    async def fail_after_patch(node: str, state: object) -> None:
        nonlocal faulted
        if (
            node == "execute_tool"
            and isinstance(state, dict)
            and state.get("successful_writes") == 1
            and not faulted
        ):
            faulted = True
            raise RuntimeError("deterministic post-patch fault")

    async with checkpoint_saver(
        os.environ["AWESOME_AGENT_TEST_CHECKPOINT_DATABASE_URL"]
    ) as saver:
        await saver.setup()
        worker = DurableWorker(
            dispatcher=PostgresRunDispatcher(sessions),
            repository=runtime,
            probe_graph=RuntimeProbeGraph(saver),
            modifying_graph=ModifyingCodingGraph(
                saver,
                provider_resolver=lambda _: provider,
                artifact_store=LocalArtifactStore(tmp_path / "artifacts"),
                artifact_repository=PostgresArtifactMetadataRepository(sessions),
                tool_repository=tool_repository,
                fault_hook=fail_after_patch,
            ),
            config=_worker_config(),
        )
        assert await worker.run_once()
        assert await worker.run_once()

    restored = await runtime.get_run(run.id)
    todos = await runtime.list_todos(run.id)
    invocations = await tool_repository.list_for_run(run.id)
    workspace = Path(restored.workspace_path or "")

    assert restored.status is RunStatus.COMPLETED
    assert restored.dispatch_status is DispatchStatus.TERMINAL
    assert restored.graph_name == MODIFYING_CODING_GRAPH
    assert todos[0].status is TodoStatus.DONE
    assert (workspace / "README.md").read_text(encoding="utf-8") == "new\n"
    assert [invocation.tool_name for invocation in invocations] == [
        "shell.execute",
        "repo.apply_patch",
        "repo.diff",
    ]
    patch_invocation = invocations[1]
    assert patch_invocation.status == "completed"
    assert patch_invocation.result_content is not None
    assert "postimage_hashes" in patch_invocation.result_content
    assert len({invocation.idempotency_key for invocation in invocations}) == 3
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
