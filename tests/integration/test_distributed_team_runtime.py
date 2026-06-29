from __future__ import annotations

import json
import os
from datetime import timedelta
from pathlib import Path

import pytest
from tests.fakes import FakeModelProvider

from awesome_agent.agents.profiles import RoleModelResolver
from awesome_agent.artifacts.store import LocalArtifactStore
from awesome_agent.domain.enums import (
    AgentKind,
    DispatchStatus,
    EventType,
    RunIntent,
    RunMode,
    RunStatus,
)
from awesome_agent.domain.models import Agent, Run
from awesome_agent.modeling import (
    AssistantMessage,
    ModelTurn,
    StopReason,
    ToolCall,
)
from awesome_agent.persistence.artifacts import PostgresArtifactMetadataRepository
from awesome_agent.persistence.database import create_engine, create_session_factory
from awesome_agent.persistence.dispatch import PostgresRunDispatcher
from awesome_agent.persistence.runtime_repository import PostgresRuntimeRepository
from awesome_agent.persistence.team import PostgresTeamRepository
from awesome_agent.runtime.graphs import TEAM_CODING_ROUTE
from awesome_agent.runtime.probe_graph import RuntimeProbeState
from awesome_agent.runtime.team_assignments import TeamAssignmentKind
from awesome_agent.runtime.team_leader_graph import TeamLeaderGraph
from awesome_agent.runtime.team_role_graph import TeamRoleGraph
from awesome_agent.runtime.team_verifier_graph import TeamVerifierGraph
from awesome_agent.runtime.worker import DurableWorker, WorkerConfig
from awesome_agent.sandbox.process import run_process

pytestmark = pytest.mark.integration


class UnusedProbeGraph:
    async def execute(self, _: Run) -> tuple[RuntimeProbeState, bool]:
        raise AssertionError("distributed team test should not execute probe graph")


@pytest.mark.skipif(
    "AWESOME_AGENT_TEST_DATABASE_URL" not in os.environ,
    reason="Integration database is not configured.",
)
async def test_distributed_team_runs_through_workers_with_lineage(
    tmp_path: Path,
) -> None:
    workspace = await _git_workspace(tmp_path)
    engine = create_engine(os.environ["AWESOME_AGENT_TEST_DATABASE_URL"])
    sessions = create_session_factory(engine)
    runtime = PostgresRuntimeRepository(sessions)
    teams = PostgresTeamRepository(sessions)
    artifacts = PostgresArtifactMetadataRepository(sessions)
    root = Run(
        goal="Coordinate teammate and verifier",
        mode=RunMode.TEAM,
        intent=RunIntent.MODIFYING,
        runtime_route=TEAM_CODING_ROUTE,
        dispatch_status=DispatchStatus.QUEUED,
        workspace_path=workspace,
    )
    root = root.model_copy(update={"graph_thread_id": f"run:{root.id}"})
    provider = FakeModelProvider(
        [
            _team_plan_json(),
            _create_subagent_turn(),
            _role_read_turn(),
            _subagent_final_turn(),
            _role_final_turn(),
            _verifier_pass_turn(),
        ]
    )
    leader = Agent(
        run_id=root.id,
        kind=AgentKind.LEADER,
        profile="leader",
        model="fake",
    )
    await runtime.create_run(root, leader)
    worker = DurableWorker(
        dispatcher=PostgresRunDispatcher(sessions),
        repository=runtime,
        probe_graph=UnusedProbeGraph(),  # type: ignore[arg-type]
        team_leader_graph=TeamLeaderGraph(
            team_repository=teams,
            provider_resolver=lambda _: provider,
            model_resolver=_models(),
            artifact_repository=artifacts,
        ),
        team_role_graph=TeamRoleGraph(
            team_repository=teams,
            provider_resolver=lambda _: provider,
            artifact_store=LocalArtifactStore(tmp_path / "artifacts"),
            artifact_repository=artifacts,
        ),
        team_verifier_graph=TeamVerifierGraph(
            team_repository=teams,
            provider_resolver=lambda _: provider,
        ),
        config=_worker_config(),
        team_repository=teams,
    )

    await _drain(worker, runtime, root.id)

    restored = await runtime.get_run(root.id)
    descendants = await runtime.list_descendant_runs(root.id)
    assignments = await teams.list_assignments(root.id, include_inactive=True)
    root_results = await teams.list_child_results(root.id)
    teammate = next(run for run in descendants if run.child_role == "teammate")
    mailbox = await teams.list_mailbox_messages(root.id)
    root_events = await runtime.list_events(root.id)

    assert restored.status is RunStatus.COMPLETED
    assert [run.child_role for run in descendants] == [
        "teammate",
        "verifier",
        "subagent",
    ]
    assert {item.kind for item in assignments} == {
        TeamAssignmentKind.TEAMMATE,
        TeamAssignmentKind.SUBAGENT,
        TeamAssignmentKind.VERIFIER,
    }
    assert all(item.status == "completed" for item in assignments)
    assert {result.status for result in root_results} == {"completed"}
    assert teammate.id
    assert mailbox[0].route == "verifier_to_leader"
    assert EventType.TEAM_CHILD_RUN_CREATED in {
        event.event_type for event in root_events
    }
    assert EventType.TEAM_PLAN_CREATED in {event.event_type for event in root_events}
    await engine.dispose()


async def _drain(
    worker: DurableWorker,
    repository: PostgresRuntimeRepository,
    run_id: object,
) -> None:
    for _ in range(15):
        assert await worker.run_once()
        if (await repository.get_run(run_id)).status is RunStatus.COMPLETED:  # type: ignore[arg-type]
            return
    raise AssertionError("distributed team run did not complete")


def _worker_config() -> WorkerConfig:
    return WorkerConfig(
        lease_duration=timedelta(seconds=60),
        heartbeat_interval=timedelta(seconds=15),
        poll_interval=0.01,
        recovery_interval=15,
        shutdown_grace=1,
        retry_delay=timedelta(seconds=0),
        max_attempts=3,
    )


def _models() -> RoleModelResolver:
    return RoleModelResolver(
        leader_model="fake-model",
        teammate_model="fake-model",
        verifier_model="fake-model",
        subagent_model="fake-model",
    )


def _team_plan_json() -> str:
    return json.dumps(
        {
            "rationale": "One teammate is enough for this skeleton run.",
            "teammates": [
                {
                    "role_profile": "backend-engineer",
                    "goal": "Inspect the repository and report a bounded result.",
                    "allowed_tools": ["repo.read", "team.create_subagent"],
                    "deferred_tools": [],
                    "allowed_skills": [],
                    "can_write": False,
                    "can_delegate": True,
                    "max_subagents": 3,
                    "acceptance_criteria": [
                        "Delegate repository inspection to a subagent.",
                    ],
                }
            ],
        }
    )


def _create_subagent_turn() -> ModelTurn:
    return ModelTurn(
        assistant=AssistantMessage(
            tool_calls=[
                ToolCall(
                    call_id="subagent-read",
                    name="team.create_subagent",
                    arguments_json=json.dumps(
                        {
                            "goal": "Read README.md and report the evidence.",
                            "allowed_tools": ["repo.read"],
                            "allowed_skills": [],
                            "acceptance_criteria": [
                                "Return README evidence to the teammate.",
                            ],
                        }
                    ),
                )
            ]
        ),
        stop_reason=StopReason.TOOL_CALLS,
        model="fake-model",
        provider="fake",
    )


def _role_read_turn() -> ModelTurn:
    return ModelTurn(
        assistant=AssistantMessage(
            tool_calls=[
                ToolCall(
                    call_id="read",
                    name="repo.read",
                    arguments_json='{"path":"README.md"}',
                )
            ]
        ),
        stop_reason=StopReason.TOOL_CALLS,
        model="fake-model",
        provider="fake",
    )


def _subagent_final_turn() -> ModelTurn:
    return ModelTurn(
        assistant=AssistantMessage(content="Subagent inspected README.md."),
        stop_reason=StopReason.COMPLETED,
        model="fake-model",
        provider="fake",
    )


def _role_final_turn() -> ModelTurn:
    return ModelTurn(
        assistant=AssistantMessage(content="Used subagent README evidence."),
        stop_reason=StopReason.COMPLETED,
        model="fake-model",
        provider="fake",
    )


def _verifier_pass_turn() -> str:
    return json.dumps(
        {
            "decision": "passed",
            "summary": "Verifier passed distributed team evidence.",
            "rework_requests": [],
            "failure_kind": None,
            "risks": [],
        }
    )


async def _git_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "repository"
    workspace.mkdir()
    await _git(workspace, "init")
    await _git(workspace, "config", "user.email", "test@example.com")
    await _git(workspace, "config", "user.name", "Test")
    (workspace / "README.md").write_text("fixture\n", encoding="utf-8")
    await _git(workspace, "add", "README.md")
    await _git(workspace, "commit", "-m", "Initial")
    return workspace


async def _git(path: Path, *arguments: str) -> None:
    result = await run_process(
        ["git", *arguments],
        command_label="git fixture",
        workspace=path,
        timeout_seconds=30,
    )
    assert result.exit_code == 0, result.stderr
