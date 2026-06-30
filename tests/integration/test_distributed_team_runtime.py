from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path
from uuid import UUID

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
    ModelRequest,
    ModelStreamEvent,
    ModelTurn,
    StopReason,
    StructuredModelProvider,
    ToolCall,
    TurnCompleted,
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
from awesome_agent.runtime.team_mailbox import MailboxMessageStatus, MailboxRoute
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


@pytest.mark.skipif(
    "AWESOME_AGENT_TEST_DATABASE_URL" not in os.environ,
    reason="Integration database is not configured.",
)
async def test_distributed_team_concurrent_workers_claim_sibling_runs_once(
    tmp_path: Path,
) -> None:
    workspace = await _git_workspace_with_validation(tmp_path)
    engine = create_engine(os.environ["AWESOME_AGENT_TEST_DATABASE_URL"])
    sessions = create_session_factory(engine)
    runtime = PostgresRuntimeRepository(sessions)
    teams = PostgresTeamRepository(sessions)
    artifacts = PostgresArtifactMetadataRepository(sessions)
    root = Run(
        goal="Concurrent distributed team stress fixture",
        mode=RunMode.TEAM,
        intent=RunIntent.MODIFYING,
        runtime_route=TEAM_CODING_ROUTE,
        dispatch_status=DispatchStatus.QUEUED,
        workspace_path=workspace,
    )
    root = root.model_copy(update={"graph_thread_id": f"run:{root.id}"})
    provider = ConcurrentStressProvider()
    leader = Agent(
        run_id=root.id,
        kind=AgentKind.LEADER,
        profile="leader",
        model="fake",
    )
    await runtime.create_run(root, leader)

    artifact_store = LocalArtifactStore(tmp_path / "artifacts")
    dispatcher = PostgresRunDispatcher(sessions)
    workers = [
        _concurrent_worker(
            worker_name=f"stress-worker-{index}",
            dispatcher=dispatcher,
            runtime=runtime,
            teams=teams,
            artifacts=artifacts,
            artifact_store=artifact_store,
            provider=provider,
        )
        for index in range(4)
    ]

    processed_counts = await _drain_concurrently(workers, runtime, root.id)

    restored = await runtime.get_run(root.id)
    assert restored is not None
    assert restored.status is RunStatus.COMPLETED
    assert max(processed_counts) >= 2

    descendants = await runtime.list_descendant_runs(root.id)
    roles = sorted(run.child_role for run in descendants if run.child_role is not None)
    assert roles.count("teammate") == 3
    assert roles.count("subagent") == 2
    assert roles.count("verifier") == 1

    assignments = await teams.list_assignments(root.id, include_inactive=True)
    teammate_assignments = [
        item for item in assignments if item.kind is TeamAssignmentKind.TEAMMATE
    ]
    assert len(teammate_assignments) == 3
    assert all(item.status == "completed" for item in assignments)
    assert len({item.child_run_id for item in assignments}) == len(assignments)

    all_runs = [restored, *descendants]
    all_results = []
    for run in all_runs:
        all_results.extend(await teams.list_child_results(run.id))
    assert len({result.child_run_id for result in all_results}) == len(all_results)

    root_results = await teams.list_child_results(root.id)
    patch_results = [
        result for result in root_results if result.patch_artifact_id is not None
    ]
    assert len(patch_results) == 1
    assert patch_results[0].patch_aggregated

    mailbox = await teams.list_mailbox_messages(root.id)
    assert [message.route for message in mailbox] == ["verifier_to_leader"]

    events = []
    for run in all_runs:
        events.extend(await runtime.list_events(run.id))
    claim_workers = {
        event.payload["worker_name"]
        for event in events
        if event.event_type is EventType.DISPATCH_CLAIMED
    }
    assert len(claim_workers) >= 2

    created_child_ids = {
        event.payload["child_run_id"]
        for event in events
        if event.event_type is EventType.TEAM_CHILD_RUN_CREATED
    }
    assert len(created_child_ids) == len(descendants)

    patch_aggregations = [
        event for event in events if event.event_type is EventType.TEAM_PATCH_AGGREGATED
    ]
    assert len(patch_aggregations) == 1

    readme = (workspace / "README.md").read_text(encoding="utf-8")
    assert "concurrent stress patch" in readme
    await engine.dispose()


@pytest.mark.skipif(
    "AWESOME_AGENT_TEST_DATABASE_URL" not in os.environ,
    reason="Integration database is not configured.",
)
async def test_distributed_team_teammates_collaborate_through_mailbox(
    tmp_path: Path,
) -> None:
    workspace = await _git_workspace(tmp_path)
    engine = create_engine(os.environ["AWESOME_AGENT_TEST_DATABASE_URL"])
    sessions = create_session_factory(engine)
    runtime = PostgresRuntimeRepository(sessions)
    teams = PostgresTeamRepository(sessions)
    artifacts = PostgresArtifactMetadataRepository(sessions)
    root = Run(
        goal="Coordinate API field naming through mailbox",
        mode=RunMode.TEAM,
        intent=RunIntent.READ_ONLY,
        runtime_route=TEAM_CODING_ROUTE,
        dispatch_status=DispatchStatus.QUEUED,
        workspace_path=workspace,
    )
    root = root.model_copy(update={"graph_thread_id": f"run:{root.id}"})
    provider = MailboxCollaborationProvider()
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
    messages = await teams.list_mailbox_messages(root.id)
    events = [
        event
        for observed_run in [restored, *descendants]
        for event in await runtime.list_events(observed_run.id)
    ]

    assert restored is not None
    assert restored.status is RunStatus.COMPLETED
    assert [message.route for message in messages] == [
        MailboxRoute.TEAMMATE_TO_TEAMMATE,
        MailboxRoute.TEAMMATE_TO_TEAMMATE,
        MailboxRoute.VERIFIER_TO_LEADER,
    ]
    assert messages[0].status is MailboxMessageStatus.RESPONDED
    assert messages[0].requires_response
    assert messages[1].response_to_message_id == messages[0].id
    assert any(
        item.allowed_tools == ["repo.read", "team.mailbox_send"]
        for item in assignments
        if item.kind is TeamAssignmentKind.TEAMMATE
    )
    assert any(
        item.allowed_tools
        == [
            "repo.read",
            "team.mailbox_list",
            "team.mailbox_send",
        ]
        for item in assignments
        if item.kind is TeamAssignmentKind.TEAMMATE
    )
    event_types = {event.event_type for event in events}
    assert EventType.TEAM_MAILBOX_MESSAGE_CREATED in event_types
    assert EventType.TEAM_MAILBOX_MESSAGE_READ in event_types
    assert EventType.TEAM_MAILBOX_MESSAGE_RESPONDED in event_types
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


def _concurrent_worker(
    *,
    worker_name: str,
    dispatcher: PostgresRunDispatcher,
    runtime: PostgresRuntimeRepository,
    teams: PostgresTeamRepository,
    artifacts: PostgresArtifactMetadataRepository,
    artifact_store: LocalArtifactStore,
    provider: StructuredModelProvider,
) -> DurableWorker:
    return DurableWorker(
        dispatcher=dispatcher,
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
            artifact_store=artifact_store,
            artifact_repository=artifacts,
        ),
        team_verifier_graph=TeamVerifierGraph(
            team_repository=teams,
            provider_resolver=lambda _: provider,
        ),
        config=_worker_config(),
        worker_name=worker_name,
        team_repository=teams,
    )


async def _drain_concurrently(
    workers: list[DurableWorker],
    repository: PostgresRuntimeRepository,
    run_id: UUID,
) -> list[int]:
    processed_counts: list[int] = []
    for _ in range(30):
        processed = await asyncio.gather(*(worker.run_once() for worker in workers))
        processed_count = sum(1 for item in processed if item)
        processed_counts.append(processed_count)

        restored = await repository.get_run(run_id)
        if restored is not None and restored.status is RunStatus.COMPLETED:
            return processed_counts
        if restored is not None and restored.status in {
            RunStatus.CANCELLED,
            RunStatus.FAILED,
            RunStatus.RECOVERY_REQUIRED,
        }:
            raise AssertionError(
                "concurrent drain reached terminal non-completed status; "
                f"status={restored.status}; processed_counts={processed_counts}"
            )

        assert processed_count > 0, (
            "concurrent drain stalled before root completion; "
            f"processed_counts={processed_counts}"
        )

    restored = await repository.get_run(run_id)
    raise AssertionError(
        "concurrent drain did not complete root run; "
        f"status={None if restored is None else restored.status}; "
        f"processed_counts={processed_counts}"
    )


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


class ConcurrentStressProvider(StructuredModelProvider):
    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []
        self._lock = asyncio.Lock()

    async def stream(
        self,
        request: ModelRequest,
    ) -> AsyncIterator[ModelStreamEvent]:
        async with self._lock:
            self.requests.append(request)

        if _is_leader_plan_request(request):
            yield _completed_text(_concurrent_team_plan_json())
            return
        if _is_verifier_request(request):
            yield _completed_text(_verifier_pass_turn())
            return
        if _is_stress_writer_request(request):
            yield TurnCompleted(turn=_stress_writer_turn(request))
            return
        if _is_stress_reader_a_request(request):
            yield TurnCompleted(turn=_delegating_teammate_turn(request, label="A"))
            return
        if _is_stress_reader_b_request(request):
            yield TurnCompleted(turn=_delegating_teammate_turn(request, label="B"))
            return
        if _is_stress_subagent_a_request(request):
            yield TurnCompleted(turn=_subagent_read_turn(request, label="A"))
            return
        if _is_stress_subagent_b_request(request):
            yield TurnCompleted(turn=_subagent_read_turn(request, label="B"))
            return
        raise AssertionError(
            "Unexpected concurrent stress request:\n"
            + "\n---\n".join(message.content for message in request.messages)
        )


class MailboxCollaborationProvider(StructuredModelProvider):
    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def stream(
        self,
        request: ModelRequest,
    ) -> AsyncIterator[ModelStreamEvent]:
        self.requests.append(request)
        if _is_leader_plan_request(request):
            yield _completed_text(_mailbox_team_plan_json())
            return
        if _is_verifier_request(request):
            yield _completed_text(_verifier_pass_turn())
            return
        if _is_mailbox_sender_request(request):
            yield TurnCompleted(turn=_mailbox_sender_turn(request))
            return
        if _is_mailbox_responder_request(request):
            yield TurnCompleted(turn=_mailbox_responder_turn(request))
            return
        raise AssertionError(
            "Unexpected mailbox collaboration request:\n"
            + "\n---\n".join(message.content for message in request.messages)
        )


def _is_leader_plan_request(request: ModelRequest) -> bool:
    return any(
        "Leader planning a coding-agent team" in message.content
        for message in request.messages
    )


def _is_verifier_request(request: ModelRequest) -> bool:
    return any(
        "independent Verifier" in message.content for message in request.messages
    )


def _is_stress_writer_request(request: ModelRequest) -> bool:
    return any(
        "Patch README.md with the concurrent worker stress marker." in message.content
        for message in request.messages
    )


def _is_stress_reader_a_request(request: ModelRequest) -> bool:
    return any(
        "Delegate README inspection A to a Subagent and report evidence."
        in message.content
        for message in request.messages
    )


def _is_stress_reader_b_request(request: ModelRequest) -> bool:
    return any(
        "Delegate README inspection B to a Subagent and report evidence."
        in message.content
        for message in request.messages
    )


def _is_stress_subagent_a_request(request: ModelRequest) -> bool:
    return any(
        "Read README.md for concurrent stress A." in message.content
        for message in request.messages
    )


def _is_stress_subagent_b_request(request: ModelRequest) -> bool:
    return any(
        "Read README.md for concurrent stress B." in message.content
        for message in request.messages
    )


def _is_mailbox_sender_request(request: ModelRequest) -> bool:
    return any(
        "Ask QA for the response field name through the mailbox." in message.content
        for message in request.messages
    )


def _is_mailbox_responder_request(request: ModelRequest) -> bool:
    return any(
        "Read mailbox coordination and answer backend." in message.content
        for message in request.messages
    )


def _has_tool_result(request: ModelRequest, call_id: str) -> bool:
    return any(
        message.role == "tool" and message.call_id == call_id
        for message in request.messages
    )


def _tool_result_payload(request: ModelRequest, call_id: str) -> dict[str, object]:
    for message in request.messages:
        if message.role == "tool" and message.call_id == call_id:
            payload = json.loads(message.content)
            assert isinstance(payload, dict)
            return payload
    raise AssertionError(f"missing tool result {call_id}")


def _has_subagent_results(request: ModelRequest) -> bool:
    return any(
        "Completed Subagent results available to this Teammate" in message.content
        for message in request.messages
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


def _concurrent_team_plan_json() -> str:
    return json.dumps(
        {
            "rationale": (
                "Concurrent Worker stress plan with one writer and two "
                "delegating readers."
            ),
            "teammates": [
                {
                    "role_profile": "backend-engineer",
                    "goal": "Patch README.md with the concurrent worker stress marker.",
                    "allowed_tools": ["repo.apply_patch", "repo.diff"],
                    "deferred_tools": [],
                    "allowed_skills": [],
                    "can_write": True,
                    "can_delegate": False,
                    "max_subagents": 0,
                    "acceptance_criteria": [
                        "README.md contains the concurrent stress marker.",
                        "Call repo.diff after the patch.",
                    ],
                },
                {
                    "role_profile": "repository-explorer",
                    "goal": (
                        "Delegate README inspection A to a Subagent and report "
                        "evidence."
                    ),
                    "allowed_tools": ["repo.read", "team.create_subagent"],
                    "deferred_tools": [],
                    "allowed_skills": [],
                    "can_write": False,
                    "can_delegate": True,
                    "max_subagents": 1,
                    "acceptance_criteria": [
                        "Subagent A reports README evidence.",
                    ],
                },
                {
                    "role_profile": "qa-engineer",
                    "goal": (
                        "Delegate README inspection B to a Subagent and report "
                        "evidence."
                    ),
                    "allowed_tools": ["repo.read", "team.create_subagent"],
                    "deferred_tools": [],
                    "allowed_skills": [],
                    "can_write": False,
                    "can_delegate": True,
                    "max_subagents": 1,
                    "acceptance_criteria": [
                        "Subagent B reports README evidence.",
                    ],
                },
            ],
        }
    )


def _mailbox_team_plan_json() -> str:
    return json.dumps(
        {
            "rationale": "Backend and QA coordinate one field name.",
            "teammates": [
                {
                    "role_profile": "backend-engineer",
                    "goal": "Ask QA for the response field name through the mailbox.",
                    "allowed_tools": ["repo.read", "team.mailbox_send"],
                    "deferred_tools": [],
                    "allowed_skills": [],
                    "can_write": False,
                    "can_delegate": False,
                    "max_subagents": 0,
                    "acceptance_criteria": [
                        "Ask QA through mailbox and read README evidence.",
                    ],
                },
                {
                    "role_profile": "qa-engineer",
                    "goal": "Read mailbox coordination and answer backend.",
                    "allowed_tools": [
                        "repo.read",
                        "team.mailbox_list",
                        "team.mailbox_send",
                    ],
                    "deferred_tools": [],
                    "allowed_skills": [],
                    "can_write": False,
                    "can_delegate": False,
                    "max_subagents": 0,
                    "acceptance_criteria": [
                        "Answer backend mailbox question and read README evidence.",
                    ],
                },
            ],
        }
    )


def _mailbox_sender_turn(request: ModelRequest) -> ModelTurn:
    if not _has_tool_result(request, "ask-qa"):
        return ModelTurn(
            assistant=AssistantMessage(
                tool_calls=[
                    ToolCall(
                        call_id="ask-qa",
                        name="team.mailbox_send",
                        arguments_json=json.dumps(
                            {
                                "recipient_run_id": _mailbox_directory_run_id(
                                    request,
                                    "qa-engineer",
                                ),
                                "message_type": "question",
                                "subject": "Response field",
                                "body_summary": (
                                    "Please confirm the response field name."
                                ),
                                "requires_response": True,
                            }
                        ),
                    )
                ]
            ),
            stop_reason=StopReason.TOOL_CALLS,
            model="fake-model",
            provider="fake",
        )
    if not _has_tool_result(request, "backend-read"):
        return ModelTurn(
            assistant=AssistantMessage(
                tool_calls=[
                    ToolCall(
                        call_id="backend-read",
                        name="repo.read",
                        arguments_json='{"path":"README.md"}',
                    )
                ]
            ),
            stop_reason=StopReason.TOOL_CALLS,
            model="fake-model",
            provider="fake",
        )
    return ModelTurn(
        assistant=AssistantMessage(
            content="Backend asked QA through mailbox and read README."
        ),
        stop_reason=StopReason.COMPLETED,
        model="fake-model",
        provider="fake",
    )


def _mailbox_responder_turn(request: ModelRequest) -> ModelTurn:
    if not _has_tool_result(request, "list-mail"):
        return ModelTurn(
            assistant=AssistantMessage(
                tool_calls=[
                    ToolCall(
                        call_id="list-mail",
                        name="team.mailbox_list",
                        arguments_json='{"limit":5}',
                    )
                ]
            ),
            stop_reason=StopReason.TOOL_CALLS,
            model="fake-model",
            provider="fake",
        )
    if not _has_tool_result(request, "answer-backend"):
        payload = _tool_result_payload(request, "list-mail")
        messages = payload["messages"]
        assert isinstance(messages, list) and messages
        question = messages[0]
        assert isinstance(question, dict)
        return ModelTurn(
            assistant=AssistantMessage(
                tool_calls=[
                    ToolCall(
                        call_id="answer-backend",
                        name="team.mailbox_send",
                        arguments_json=json.dumps(
                            {
                                "recipient_run_id": question["sender_run_id"],
                                "message_type": "status",
                                "subject": "Response field",
                                "body_summary": "Use response_text.",
                                "response_to_message_id": question["id"],
                            }
                        ),
                    )
                ]
            ),
            stop_reason=StopReason.TOOL_CALLS,
            model="fake-model",
            provider="fake",
        )
    if not _has_tool_result(request, "qa-read"):
        return ModelTurn(
            assistant=AssistantMessage(
                tool_calls=[
                    ToolCall(
                        call_id="qa-read",
                        name="repo.read",
                        arguments_json='{"path":"README.md"}',
                    )
                ]
            ),
            stop_reason=StopReason.TOOL_CALLS,
            model="fake-model",
            provider="fake",
        )
    return ModelTurn(
        assistant=AssistantMessage(
            content="QA answered backend through mailbox and read README."
        ),
        stop_reason=StopReason.COMPLETED,
        model="fake-model",
        provider="fake",
    )


def _mailbox_directory_run_id(request: ModelRequest, role_profile: str) -> str:
    prefix = f"- teammate {role_profile}: run_id="
    for message in request.messages:
        for line in message.content.splitlines():
            if line.startswith(prefix):
                return line.removeprefix(prefix)
    raise AssertionError(f"mailbox directory missing {role_profile}")


def _stress_writer_turn(request: ModelRequest) -> ModelTurn:
    if not _has_tool_result(request, "stress-patch"):
        return ModelTurn(
            assistant=AssistantMessage(
                tool_calls=[
                    ToolCall(
                        call_id="stress-patch",
                        name="repo.apply_patch",
                        arguments_json=json.dumps({"patch": _stress_patch()}),
                    )
                ]
            ),
            stop_reason=StopReason.TOOL_CALLS,
            model="fake-model",
            provider="fake",
        )
    if not _has_tool_result(request, "stress-diff"):
        return ModelTurn(
            assistant=AssistantMessage(
                tool_calls=[
                    ToolCall(
                        call_id="stress-diff",
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
            content="Writer complete: README.md contains concurrent stress patch."
        ),
        stop_reason=StopReason.COMPLETED,
        model="fake-model",
        provider="fake",
    )


def _delegating_teammate_turn(request: ModelRequest, *, label: str) -> ModelTurn:
    if not _has_subagent_results(request):
        return ModelTurn(
            assistant=AssistantMessage(
                tool_calls=[
                    ToolCall(
                        call_id=f"create-subagent-{label}",
                        name="team.create_subagent",
                        arguments_json=json.dumps(
                            {
                                "goal": (
                                    f"Read README.md for concurrent stress {label}."
                                ),
                                "allowed_tools": ["repo.read"],
                                "allowed_skills": [],
                                "acceptance_criteria": [
                                    (
                                        "Report README evidence for concurrent "
                                        f"stress {label}."
                                    )
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
    return ModelTurn(
        assistant=AssistantMessage(
            content=f"Reader {label} complete: Subagent {label} reported evidence."
        ),
        stop_reason=StopReason.COMPLETED,
        model="fake-model",
        provider="fake",
    )


def _subagent_read_turn(request: ModelRequest, *, label: str) -> ModelTurn:
    call_id = f"subagent-{label}-read"
    if not _has_tool_result(request, call_id):
        return ModelTurn(
            assistant=AssistantMessage(
                tool_calls=[
                    ToolCall(
                        call_id=call_id,
                        name="repo.read",
                        arguments_json='{"path":"README.md"}',
                    )
                ]
            ),
            stop_reason=StopReason.TOOL_CALLS,
            model="fake-model",
            provider="fake",
        )
    return ModelTurn(
        assistant=AssistantMessage(
            content=f"Subagent {label} complete: README.md was read successfully."
        ),
        stop_reason=StopReason.COMPLETED,
        model="fake-model",
        provider="fake",
    )


def _stress_patch() -> str:
    return (
        "diff --git a/README.md b/README.md\n"
        "--- a/README.md\n"
        "+++ b/README.md\n"
        "@@ -1 +1,2 @@\n"
        " fixture\n"
        "+concurrent stress patch\n"
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


async def _git_workspace_with_validation(tmp_path: Path) -> Path:
    workspace = tmp_path / "validated-repository"
    workspace.mkdir()
    await _git(workspace, "init")
    await _git(workspace, "config", "user.email", "test@example.com")
    await _git(workspace, "config", "user.name", "Test")
    (workspace / "README.md").write_text("fixture\n", encoding="utf-8")
    (workspace / "pytest").write_bytes(
        (
            "#!/usr/local/bin/python\n"
            "from pathlib import Path\n"
            "assert 'concurrent stress patch' in Path('README.md').read_text()\n"
        ).encode("ascii")
    )
    (workspace / ".gitattributes").write_text(
        "pytest text eol=lf\n",
        encoding="utf-8",
    )
    validation_dir = workspace / ".agents"
    validation_dir.mkdir()
    (validation_dir / "validation.toml").write_text(
        "\n".join(
            [
                "version = 1",
                "",
                "[[gates]]",
                'id = "concurrent-stress-marker"',
                'name = "Concurrent stress marker"',
                'command = ["./pytest"]',
                "required = true",
                "timeout_seconds = 30",
                "",
            ]
        ),
        encoding="utf-8",
    )
    await _git(
        workspace,
        "add",
        ".agents/validation.toml",
        ".gitattributes",
        "README.md",
        "pytest",
    )
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
