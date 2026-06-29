from __future__ import annotations

import json
import os
from collections import deque
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from pydantic import SecretStr

from awesome_agent.agents.profiles import RoleModelResolver
from awesome_agent.domain.enums import (
    AgentKind,
    EventType,
    RunIntent,
    RunMode,
    RunStatus,
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
from awesome_agent.observability.repository import PostgresObservabilityRepository
from awesome_agent.persistence.artifacts import PostgresArtifactMetadataRepository
from awesome_agent.persistence.database import create_engine, create_session_factory
from awesome_agent.persistence.intake_reservations import (
    PostgresIntakeReservationStore,
)
from awesome_agent.persistence.repository_registry import PostgresRepositoryRegistry
from awesome_agent.persistence.runtime_repository import PostgresRuntimeRepository
from awesome_agent.persistence.team import PostgresTeamRepository
from awesome_agent.providers.factory import ModelProviderFactory
from awesome_agent.repositories.git import require_primary_clean_repository
from awesome_agent.repositories.worktrees import ManagedRunWorktreeManager
from awesome_agent.runtime.events import EventStream
from awesome_agent.runtime.intake import RunIntakeService
from awesome_agent.runtime.worker_app import run_worker
from awesome_agent.sandbox.process import run_process
from awesome_agent.settings import Settings

pytestmark = pytest.mark.e2e


@pytest.mark.skipif(
    "AWESOME_AGENT_TEST_DATABASE_URL" not in os.environ
    or "AWESOME_AGENT_TEST_CHECKPOINT_DATABASE_URL" not in os.environ,
    reason="Runtime and checkpoint databases are not configured.",
)
async def test_team_run_completes_as_distributed_child_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_path = await _git_workspace(tmp_path)
    snapshot = await require_primary_clean_repository(repository_path)
    engine = create_engine(os.environ["AWESOME_AGENT_TEST_DATABASE_URL"])
    sessions = create_session_factory(engine)
    registry = PostgresRepositoryRegistry(sessions)
    registered = await registry.upsert(
        Repository(
            root=snapshot.root,
            display_name="distributed-team-fixture",
            git_common_dir=snapshot.git_common_dir,
            default_branch=snapshot.branch,
        )
    )
    runtime = PostgresRuntimeRepository(sessions)
    teams = PostgresTeamRepository(sessions)
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
        goal="Use a teammate and verifier to inspect the repository",
        intent=RunIntent.MODIFYING,
        mode=RunMode.TEAM,
    )
    provider = DynamicHappyPathProvider()
    monkeypatch.setattr(ModelProviderFactory, "create", lambda _self, _model: provider)
    settings = Settings(
        database_url=os.environ["AWESOME_AGENT_TEST_DATABASE_URL"],
        checkpoint_database_url=os.environ[
            "AWESOME_AGENT_TEST_CHECKPOINT_DATABASE_URL"
        ],
        deepseek_api_key=SecretStr("fake"),
        artifact_root=tmp_path / "artifacts",
        worker_poll_interval_seconds=0.01,
        max_claim_attempts=10,
    )

    for _ in range(15):
        processed = await run_worker(once=True, settings=settings)
        if (await runtime.get_run(run.id)).status is RunStatus.COMPLETED:
            break
        assert processed
    else:
        raise AssertionError("distributed team run did not complete")

    restored = await runtime.get_run(run.id)
    agents = await runtime.list_agents(run.id)
    descendants = await runtime.list_descendant_runs(run.id)
    assignments = await teams.list_assignments(run.id, include_inactive=True)
    results = await teams.list_child_results(run.id)
    messages = await teams.list_mailbox_messages(run.id)
    observed_runs = [restored, *descendants]
    events = [
        event
        for observed_run in observed_runs
        for event in await runtime.list_events(observed_run.id)
    ]
    observability = PostgresObservabilityRepository(sessions)
    model_calls = [
        call
        for observed_run in observed_runs
        for call in await observability.list_model_calls_for_run(observed_run.id)
    ]
    spans = [
        span
        for observed_run in observed_runs
        for span in await observability.list_spans_for_run(observed_run.id)
    ]
    artifact_repository = PostgresArtifactMetadataRepository(sessions)
    artifacts = [
        artifact
        for observed_run in descendants
        for artifact in await artifact_repository.list_for_run(observed_run.id)
    ]
    workspace = Path(restored.workspace_path or "")

    assert restored.status is RunStatus.COMPLETED
    assert restored.runtime_route == "team-coding"
    assert not hasattr(restored, "graph_version")
    assert [agent.kind for agent in agents] == [AgentKind.LEADER]
    child_roles = [run.child_role for run in descendants]
    assert child_roles.count("teammate") == 2
    assert child_roles.count("subagent") == 1
    assert child_roles.count("verifier") == 1
    assert {assignment.kind.value for assignment in assignments} == {
        "teammate",
        "subagent",
        "verifier",
    }
    assert all(assignment.status.value == "completed" for assignment in assignments)
    assert sum(assignment.kind.value == "teammate" for assignment in assignments) == 2
    assert any(result.patch_artifact_id for result in results)
    assert all(
        result.patch_aggregated
        for result in results
        if result.patch_artifact_id is not None
    )
    assert "team patch" in (workspace / "README.md").read_text(encoding="utf-8")
    assert messages[0].route.value == "verifier_to_leader"
    assert {event.event_type for event in events} >= {
        EventType.MODEL_CALL_CREATED,
        EventType.TOOL_CALL_CREATED,
        EventType.TEAM_PATCH_AGGREGATED,
    }
    assert {span.name for span in spans} >= {"model.call", "tool.call"}
    assert len(model_calls) >= 7
    assert any(artifact.artifact_type == "patch" for artifact in artifacts)
    await engine.dispose()


@pytest.mark.skipif(
    "AWESOME_AGENT_TEST_DATABASE_URL" not in os.environ
    or "AWESOME_AGENT_TEST_CHECKPOINT_DATABASE_URL" not in os.environ,
    reason="Runtime and checkpoint databases are not configured.",
)
async def test_team_run_reworks_after_verifier_rejection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_path = await _git_workspace(tmp_path)
    snapshot = await require_primary_clean_repository(repository_path)
    engine = create_engine(os.environ["AWESOME_AGENT_TEST_DATABASE_URL"])
    sessions = create_session_factory(engine)
    registry = PostgresRepositoryRegistry(sessions)
    registered = await registry.upsert(
        Repository(
            root=snapshot.root,
            display_name="distributed-team-rework-fixture",
            git_common_dir=snapshot.git_common_dir,
            default_branch=snapshot.branch,
        )
    )
    runtime = PostgresRuntimeRepository(sessions)
    teams = PostgresTeamRepository(sessions)
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
        goal="Inspect the repository, rework if verifier rejects evidence",
        intent=RunIntent.MODIFYING,
        mode=RunMode.TEAM,
    )
    provider = DynamicReworkProvider()
    monkeypatch.setattr(ModelProviderFactory, "create", lambda _self, _model: provider)
    settings = Settings(
        database_url=os.environ["AWESOME_AGENT_TEST_DATABASE_URL"],
        checkpoint_database_url=os.environ[
            "AWESOME_AGENT_TEST_CHECKPOINT_DATABASE_URL"
        ],
        deepseek_api_key=SecretStr("fake"),
        artifact_root=tmp_path / "artifacts",
        worker_poll_interval_seconds=0.01,
        max_claim_attempts=10,
    )

    for _ in range(25):
        processed = await run_worker(once=True, settings=settings)
        if (await runtime.get_run(run.id)).status is RunStatus.COMPLETED:
            break
        assert processed
    else:
        raise AssertionError("distributed team rework run did not complete")

    assignments = await teams.list_assignments(run.id, include_inactive=True)
    results = await teams.list_child_results(run.id)
    messages = await teams.list_mailbox_messages(run.id)
    events = await runtime.list_events(run.id)

    assert sum(item.kind.value == "teammate" for item in assignments) == 2
    assert any(
        item.handoff_context.get("previous_assignment_id") for item in assignments
    )
    assert any(item.status.value == "retired" for item in assignments)
    assert {result.status for result in results} == {"completed", "failed"}
    assert any(message.requires_response for message in messages)
    assert any(not message.requires_response for message in messages)
    assert EventType.TEAM_REWORK_REQUESTED in {event.event_type for event in events}
    await engine.dispose()


def _models() -> RoleModelResolver:
    return RoleModelResolver(
        leader_model="fake-model",
        teammate_model="fake-model",
        verifier_model="fake-model",
        subagent_model="fake-model",
    )


class DynamicReworkProvider(StructuredModelProvider):
    def __init__(self) -> None:
        self._responses = deque(
            [
                _team_plan_without_subagent_json(),
                _role_read_turn(),
                _role_final_turn(),
                _role_read_turn(),
                _role_final_turn(),
            ]
        )
        self._verifier_calls = 0
        self.requests: list[ModelRequest] = []

    async def stream(
        self,
        request: ModelRequest,
    ) -> AsyncIterator[ModelStreamEvent]:
        self.requests.append(request)
        if _is_verifier_request(request):
            self._verifier_calls += 1
            if self._verifier_calls == 1:
                text = _verifier_rework_turn(_first_child_result_id(request))
            else:
                text = _verifier_pass_turn()
            yield TurnCompleted(
                turn=ModelTurn(
                    assistant=AssistantMessage(content=text),
                    stop_reason=StopReason.COMPLETED,
                    model="fake-model",
                    provider="fake",
                )
            )
            return
        response = self._responses.popleft()
        if isinstance(response, ModelTurn):
            yield TurnCompleted(turn=response)
            return
        text = str(response)
        yield TurnCompleted(
            turn=ModelTurn(
                assistant=AssistantMessage(content=text),
                stop_reason=StopReason.COMPLETED,
                model="fake-model",
                provider="fake",
            )
        )


class DynamicHappyPathProvider(StructuredModelProvider):
    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def stream(
        self,
        request: ModelRequest,
    ) -> AsyncIterator[ModelStreamEvent]:
        self.requests.append(request)
        if _is_leader_plan_request(request):
            yield _completed_text(_team_plan_json())
            return
        if _is_verifier_request(request):
            yield _completed_text(_verifier_pass_turn())
            return
        if _is_patch_teammate_request(request):
            yield TurnCompleted(turn=_patch_teammate_turn(request))
            return
        if _is_read_request(request):
            yield TurnCompleted(turn=_read_or_final_turn(request))
            return
        raise AssertionError(
            "Unexpected model request:\n"
            + "\n---\n".join(message.content for message in request.messages)
        )


def _is_verifier_request(request: ModelRequest) -> bool:
    return any(
        "independent Verifier" in message.content for message in request.messages
    )


def _is_leader_plan_request(request: ModelRequest) -> bool:
    return any(
        "Leader planning a coding-agent team" in message.content
        for message in request.messages
    )


def _is_patch_teammate_request(request: ModelRequest) -> bool:
    return any("Patch README.md" in message.content for message in request.messages)


def _is_read_request(request: ModelRequest) -> bool:
    return any("Read README.md" in message.content for message in request.messages)


def _has_tool_result(request: ModelRequest, call_id: str) -> bool:
    return any(
        message.role == "tool" and message.call_id == call_id
        for message in request.messages
    )


def _has_subagent_results(request: ModelRequest) -> bool:
    return any(
        "Completed Subagent results available to this Teammate" in message.content
        for message in request.messages
    )


def _first_child_result_id(request: ModelRequest) -> str:
    payload = json.loads(request.messages[-1].content)
    return str(payload["child_results"][0]["child_run_id"])


def _team_plan_json() -> str:
    return json.dumps(
        {
            "rationale": "Patch and read evidence need separate teammates.",
            "teammates": [
                {
                    "role_profile": "backend-engineer",
                    "goal": "Patch README.md after delegating repository evidence.",
                    "allowed_tools": [
                        "repo.read",
                        "repo.apply_patch",
                        "repo.diff",
                        "team.create_subagent",
                    ],
                    "deferred_tools": [],
                    "allowed_skills": [],
                    "can_write": True,
                    "can_delegate": True,
                    "max_subagents": 3,
                    "acceptance_criteria": [
                        "Delegate repository inspection to a subagent.",
                        "Apply a README.md patch.",
                        "Call repo.diff after the patch.",
                    ],
                },
                {
                    "role_profile": "qa-engineer",
                    "goal": "Read README.md and report bounded evidence.",
                    "allowed_tools": ["repo.read"],
                    "deferred_tools": [],
                    "allowed_skills": [],
                    "can_write": False,
                    "can_delegate": False,
                    "max_subagents": 0,
                    "acceptance_criteria": [
                        "Return README evidence without writing files.",
                    ],
                },
            ],
        }
    )


def _team_plan_without_subagent_json() -> str:
    return json.dumps(
        {
            "rationale": "One teammate can inspect the repository.",
            "teammates": [
                {
                    "role_profile": "backend-engineer",
                    "goal": "Inspect the repository and report evidence.",
                    "allowed_tools": ["repo.read"],
                    "deferred_tools": [],
                    "allowed_skills": [],
                    "can_write": False,
                    "can_delegate": False,
                    "max_subagents": 0,
                    "acceptance_criteria": ["Return repository evidence."],
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


def _patch_teammate_turn(request: ModelRequest) -> ModelTurn:
    if not _has_subagent_results(request):
        return _create_subagent_turn()
    if not _has_tool_result(request, "patch-readme"):
        return ModelTurn(
            assistant=AssistantMessage(
                tool_calls=[
                    ToolCall(
                        call_id="patch-readme",
                        name="repo.apply_patch",
                        arguments_json=json.dumps({"patch": _readme_patch()}),
                    )
                ]
            ),
            stop_reason=StopReason.TOOL_CALLS,
            model="fake-model",
            provider="fake",
        )
    if not _has_tool_result(request, "patch-diff"):
        return ModelTurn(
            assistant=AssistantMessage(
                tool_calls=[
                    ToolCall(
                        call_id="patch-diff",
                        name="repo.diff",
                        arguments_json="{}",
                    )
                ]
            ),
            stop_reason=StopReason.TOOL_CALLS,
            model="fake-model",
            provider="fake",
        )
    return ModelTurn(
        assistant=AssistantMessage(
            content="Patched README.md after subagent evidence."
        ),
        stop_reason=StopReason.COMPLETED,
        model="fake-model",
        provider="fake",
    )


def _read_or_final_turn(request: ModelRequest) -> ModelTurn:
    if _has_tool_result(request, "read"):
        return _role_final_turn()
    return _role_read_turn()


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


def _readme_patch() -> str:
    return (
        "diff --git a/README.md b/README.md\n"
        "--- a/README.md\n"
        "+++ b/README.md\n"
        "@@ -1 +1,2 @@\n"
        " fixture\n"
        "+team patch\n"
    )


def _completed_text(text: str) -> TurnCompleted:
    return TurnCompleted(
        turn=ModelTurn(
            assistant=AssistantMessage(content=text),
            stop_reason=StopReason.COMPLETED,
            model="fake-model",
            provider="fake",
        )
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


def _verifier_rework_turn(target_child_run_id: str) -> str:
    return json.dumps(
        {
            "decision": "rework_required",
            "summary": "Verifier needs stronger repository evidence.",
            "rework_requests": [
                {
                    "target_child_run_id": target_child_run_id,
                    "reason": "The first attempt did not provide enough evidence.",
                    "acceptance_criteria": ["Read README.md and summarize it."],
                }
            ],
            "failure_kind": "model_output_failure",
            "risks": [],
        }
    )


async def _git_workspace(tmp_path: Path) -> Path:
    repository_path = tmp_path / "repository"
    repository_path.mkdir()
    await _git(repository_path, "init")
    await _git(repository_path, "config", "user.email", "test@example.com")
    await _git(repository_path, "config", "user.name", "Test")
    (repository_path / "README.md").write_text("fixture\n", encoding="utf-8")
    await _git(repository_path, "add", "README.md")
    await _git(repository_path, "commit", "-m", "Initial")
    return repository_path


async def _git(path: Path, *arguments: str) -> None:
    result = await run_process(
        ["git", *arguments],
        command_label="git fixture",
        workspace=path,
        timeout_seconds=30,
    )
    assert result.exit_code == 0, result.stderr
