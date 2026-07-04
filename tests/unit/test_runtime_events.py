from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from awesome_agent.agents.profiles import RoleModelResolver
from awesome_agent.artifacts.store import LocalArtifactStore
from awesome_agent.domain.enums import (
    AgentKind,
    ApprovalStatus,
    DispatchStatus,
    EventType,
    RunStatus,
)
from awesome_agent.domain.models import Agent, Run, RuntimeEvent
from awesome_agent.persistence.approvals import (
    DurableApproval,
    InMemoryApprovalRepository,
)
from awesome_agent.runtime.dispatch import DispatchConflict
from awesome_agent.runtime.events import EventStream
from awesome_agent.runtime.repository import InMemoryRuntimeRepository
from awesome_agent.runtime.service import RuntimeService


def _models() -> RoleModelResolver:
    return RoleModelResolver(
        leader_model="deepseek-v4-pro",
        teammate_model="deepseek-v4-flash",
        verifier_model="deepseek-v4-flash",
        subagent_model="deepseek-v4-flash",
    )


class FakeCancellationDispatcher:
    def __init__(self) -> None:
        self.cancelled: list[UUID] = []
        self.approval_requeues: list[dict[str, object]] = []

    async def request_cancellation(
        self,
        *,
        run_id: UUID,
        requested_by: str | None,
        reason: str | None,
    ) -> RuntimeEvent:
        self.cancelled.append(run_id)
        return RuntimeEvent(
            run_id=run_id,
            sequence=99,
            event_type=EventType.CANCELLATION_REQUESTED,
            payload={"requested_by": requested_by, "reason": reason},
        )

    async def requeue_after_approval(
        self,
        *,
        run_id: UUID,
        approval_id: UUID,
        reason: str,
    ) -> None:
        self.approval_requeues.append(
            {
                "run_id": run_id,
                "approval_id": approval_id,
                "reason": reason,
            }
        )


@pytest.mark.asyncio
async def test_runtime_service_emits_traceable_events(tmp_path: Path) -> None:
    events = EventStream()
    service = RuntimeService(
        repository=InMemoryRuntimeRepository(),
        events=events,
        artifacts=LocalArtifactStore(tmp_path),
        model_resolver=_models(),
    )

    run = await service.create_run("Implement feature")
    cancelled = await service.cancel_run(run.id)
    repeated = await service.cancel_run(run.id)
    history = await service.list_events(run.id)

    assert cancelled.status is RunStatus.CANCELLED
    assert repeated.status is RunStatus.CANCELLED
    assert [event.sequence for event in history] == [1, 2, 3]
    assert history[1].agent_id == (await service.list_agents(run.id))[0].id


@pytest.mark.asyncio
async def test_event_stream_replays_after_cursor(tmp_path: Path) -> None:
    events = EventStream()
    service = RuntimeService(
        repository=InMemoryRuntimeRepository(),
        events=events,
        artifacts=LocalArtifactStore(tmp_path),
        model_resolver=_models(),
    )
    run = await service.create_run("Goal")

    history = await service.list_events(run.id, after_sequence=1)
    assert [event.sequence for event in history] == [2]

    subscription = service.stream_events(run.id, after_sequence=1)
    replayed = await anext(subscription)
    await subscription.aclose()

    assert replayed.sequence == 2


@pytest.mark.asyncio
async def test_claimed_run_cancellation_is_rejected(tmp_path: Path) -> None:
    repository = InMemoryRuntimeRepository()
    service = RuntimeService(
        repository=repository,
        events=EventStream(),
        artifacts=LocalArtifactStore(tmp_path),
        model_resolver=_models(),
    )
    run = await service.create_run("Goal")
    await repository.update_run(
        run.model_copy(update={"dispatch_status": DispatchStatus.CLAIMED})
    )

    with pytest.raises(DispatchConflict):
        await service.cancel_run(run.id)


@pytest.mark.asyncio
async def test_runtime_service_does_not_fake_resume_by_setting_running(
    tmp_path: Path,
) -> None:
    repository = InMemoryRuntimeRepository()
    service = RuntimeService(
        repository=repository,
        events=EventStream(),
        artifacts=LocalArtifactStore(tmp_path),
        model_resolver=_models(),
    )
    run = await service.create_run("Goal")
    await repository.update_run(run.model_copy(update={"status": RunStatus.PAUSED}))

    with pytest.raises(ValueError, match="thread continuation"):
        await service.resume_run(run.id)

    current = await repository.get_run(run.id)
    assert current.status is RunStatus.PAUSED


@pytest.mark.asyncio
async def test_in_memory_runtime_events_redact_payload_before_storage() -> None:
    repository = InMemoryRuntimeRepository()
    run = Run(goal="redact")
    leader = Agent(
        run_id=run.id,
        kind=AgentKind.LEADER,
        profile="leader",
        model="fake-model",
    )
    await repository.create_run(run, leader)

    await repository.append_event(
        run_id=run.id,
        event_type=EventType.TOOL_CALL_CREATED,
        payload={
            "result_summary": (
                "OPENAI_API_KEY=sk-proj-abcdefghijklmnopqrstuvwxyz1234567890"
            ),
            "guardrails": {
                "version": 1,
                "assessments": [
                    {
                        "subject": "command",
                        "operation": "execute",
                        "decision": "ask",
                        "severity": "medium",
                        "reason": "token=abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG",
                        "rule_ids": ["guard.command.sensitive_target"],
                        "targets": [
                            {
                                "kind": "command",
                                "value": (
                                    "echo token="
                                    "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG"
                                ),
                                "sensitivity": "sensitive",
                            }
                        ],
                        "approval_scope": None,
                        "bypass_used": False,
                        "stats": {},
                    }
                ],
            },
        },
    )

    [event] = await repository.list_events(run.id)
    serialized = str(event.payload)
    assert "sk-proj-" not in serialized
    assert "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG" not in serialized
    assert event.payload["guardrails"]["assessments"][0]["decision"] == "ask"
    assert event.payload["redaction"]["applied"] is True


@pytest.mark.asyncio
async def test_dispatcher_backed_cancellation_accepts_claimed_run(
    tmp_path: Path,
) -> None:
    repository = InMemoryRuntimeRepository()
    dispatcher = FakeCancellationDispatcher()
    service = RuntimeService(
        repository=repository,
        events=EventStream(),
        artifacts=LocalArtifactStore(tmp_path),
        model_resolver=_models(),
        dispatcher=dispatcher,  # type: ignore[arg-type]
    )
    run = await service.create_run("Goal")
    await repository.update_run(
        run.model_copy(update={"dispatch_status": DispatchStatus.CLAIMED})
    )

    current = await service.cancel_run(run.id)

    assert current.dispatch_status is DispatchStatus.CLAIMED
    assert dispatcher.cancelled == [run.id]


@pytest.mark.asyncio
async def test_dispatcher_backed_approval_decision_requeues_waiting_run(
    tmp_path: Path,
) -> None:
    repository = InMemoryRuntimeRepository()
    approvals = InMemoryApprovalRepository()
    dispatcher = FakeCancellationDispatcher()
    service = RuntimeService(
        repository=repository,
        events=EventStream(),
        artifacts=LocalArtifactStore(tmp_path),
        model_resolver=_models(),
        approval_repository=approvals,
        dispatcher=dispatcher,  # type: ignore[arg-type]
    )
    run = await service.create_run("Goal")
    await repository.update_run(
        run.model_copy(
            update={
                "status": RunStatus.PAUSED,
                "dispatch_status": DispatchStatus.WAITING,
            }
        )
    )
    approval_id = UUID("00000000-0000-0000-0000-000000000123")
    await approvals.upsert(
        DurableApproval(
            id=approval_id,
            run_id=run.id,
            tool_invocation_id=approval_id,
            tool_call_id="call_shell",
            tool_name="shell.execute",
            tool_version="1",
            canonical_arguments={"argv": ["wc", "-l", "index.html"]},
            arguments_hash="hash",
            workspace_path=str(tmp_path),
            workspace_fingerprint="fingerprint",
            capabilities=["shell:execute"],
            risk_level="high",
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
    )

    event = await service.decide_approval(
        run.id,
        approval_id=approval_id,
        approved=True,
    )

    assert event.event_type is EventType.APPROVAL_DECIDED
    decided = await approvals.get(approval_id)
    assert decided.status is ApprovalStatus.APPROVED
    assert decided.decided_by == "api"
    assert dispatcher.approval_requeues == [
        {
            "run_id": run.id,
            "approval_id": approval_id,
            "reason": "approval_decided",
        }
    ]
