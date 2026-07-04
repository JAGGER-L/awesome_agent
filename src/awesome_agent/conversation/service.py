from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Protocol
from uuid import UUID, uuid4

from awesome_agent.conversation.events import (
    ConversationStreamEvent,
    ConversationStreamEventKind,
)
from awesome_agent.conversation.models import ThreadMessageRole
from awesome_agent.conversation.repository import ConversationRepository
from awesome_agent.domain.enums import (
    DispatchStatus,
    EventType,
    RunStatus,
)
from awesome_agent.domain.models import Run, RuntimeEvent
from awesome_agent.modeling.errors import (
    ModelErrorCode,
    ModelErrorInfo,
)
from awesome_agent.runtime.repository import RuntimeRepository


class ConversationRunIntake(Protocol):
    async def create_turn_run(
        self,
        *,
        thread_id: UUID,
        content: str,
        model: str | None,
        thinking: str | None,
        memory: dict[str, object],
        skill_ids: tuple[str, ...],
    ) -> Run:
        pass


class ConversationService:
    def __init__(
        self,
        *,
        repository: ConversationRepository,
        runtime_repository: RuntimeRepository,
        conversation_run_intake: ConversationRunIntake,
        default_model: str,
        event_poll_interval: float = 0.05,
    ) -> None:
        self._repository = repository
        self._runtime_repository = runtime_repository
        self._conversation_run_intake = conversation_run_intake
        self._default_model = default_model
        self._event_poll_interval = event_poll_interval

    async def start_turn(
        self,
        *,
        thread_id: UUID,
        content: str,
        model: str | None = None,
        thinking: str | None = None,
        memory: dict[str, object] | None = None,
        skill_ids: tuple[str, ...] = (),
    ) -> AsyncIterator[ConversationStreamEvent]:
        turn_id = uuid4()
        trace_id = uuid4().hex
        sequence = 1
        run = await self._conversation_run_intake.create_turn_run(
            thread_id=thread_id,
            content=content,
            model=model,
            thinking=thinking,
            memory=memory or {},
            skill_ids=skill_ids,
        )
        yield _event(
            ConversationStreamEventKind.TURN_STARTED,
            thread_id=thread_id,
            turn_id=turn_id,
            sequence=sequence,
            trace_id=trace_id,
            payload={
                "run_id": str(run.id),
                "status": run.status.value,
                "model": model or self._default_model,
            },
        )
        async for projected in self._project_run_events(
            thread_id=thread_id,
            turn_id=turn_id,
            trace_id=trace_id,
            run_id=run.id,
            after_sequence=0,
        ):
            sequence += 1
            yield projected.model_copy(update={"sequence": sequence})

    async def continue_turn(
        self,
        *,
        thread_id: UUID,
        expected_run_id: UUID | None = None,
    ) -> AsyncIterator[ConversationStreamEvent]:
        await self._repository.get_thread(thread_id)
        run = await self.latest_resumable_thread_run(thread_id)
        if run is None:
            raise ValueError("no_resumable_turn")
        if expected_run_id is not None and expected_run_id != run.id:
            raise ValueError("resumable_run_changed")

        stream_id = uuid4()
        trace_id = uuid4().hex
        sequence = 1
        yield _event(
            ConversationStreamEventKind.TURN_CONTINUED,
            thread_id=thread_id,
            turn_id=stream_id,
            sequence=sequence,
            trace_id=trace_id,
            payload={
                "run_id": str(run.id),
                "stream_id": str(stream_id),
                "status": run.status.value,
                "dispatch_status": run.dispatch_status.value,
                "resumed": True,
            },
        )
        async for projected in self._project_run_events(
            thread_id=thread_id,
            turn_id=stream_id,
            trace_id=trace_id,
            run_id=run.id,
            after_sequence=0,
        ):
            sequence += 1
            yield projected.model_copy(update={"sequence": sequence})

    async def latest_resumable_thread_run(self, thread_id: UUID) -> Run | None:
        await self._repository.get_thread(thread_id)
        candidates: list[tuple[RuntimeEvent, Run]] = []
        for run in await self._runtime_repository.list_runs():
            if not _is_resumable_run(run):
                continue
            created_event = await self._run_created_event(run.id)
            if created_event is None:
                continue
            if created_event.payload.get("thread_id") != str(thread_id):
                continue
            candidates.append((created_event, run))
        if not candidates:
            return None
        candidates.sort(
            key=lambda item: (
                item[0].created_at,
                item[1].created_at,
                item[1].id.hex,
            ),
            reverse=True,
        )
        return candidates[0][1]

    async def list_thread_runs(self, thread_id: UUID) -> list[dict[str, object]]:
        await self._repository.get_thread(thread_id)
        projections: list[tuple[RuntimeEvent, Run, dict[str, object]]] = []
        for run in await self._runtime_repository.list_runs():
            created_event = await self._run_created_event(run.id)
            if created_event is None:
                continue
            created_payload = created_event.payload
            if created_payload.get("thread_id") != str(thread_id):
                continue
            projections.append(
                (
                    created_event,
                    run,
                    {
                        "run_id": str(run.id),
                        "thread_id": str(thread_id),
                        "goal": str(created_payload.get("goal") or run.goal),
                        "status": run.status.value,
                        "dispatch_status": run.dispatch_status.value,
                        "runtime_route": run.runtime_route,
                        "execution_kind": run.execution_kind.value,
                        "result_text": run.result_text,
                    },
                )
            )
        projections.sort(
            key=lambda item: (item[0].created_at, item[1].created_at, item[1].id.hex),
            reverse=True,
        )
        return [projection for _event, _run, projection in projections]

    async def _run_created_event(self, run_id: UUID) -> RuntimeEvent | None:
        for event in await self._runtime_repository.list_events(run_id):
            if event.event_type is EventType.RUN_CREATED:
                return event
        return None

    async def _project_run_events(
        self,
        *,
        thread_id: UUID,
        turn_id: UUID,
        trace_id: str,
        run_id: UUID,
        after_sequence: int,
    ) -> AsyncIterator[ConversationStreamEvent]:
        last_sequence = after_sequence
        while True:
            runtime_events = await self._runtime_repository.list_events(
                run_id,
                after_sequence=last_sequence,
            )
            if not runtime_events:
                await asyncio.sleep(max(self._event_poll_interval, 0.001))
                continue
            for runtime_event in runtime_events:
                last_sequence = max(last_sequence, runtime_event.sequence)
                projected = self._project_runtime_event(
                    runtime_event,
                    thread_id=thread_id,
                    turn_id=turn_id,
                    trace_id=trace_id,
                )
                if projected is not None:
                    yield projected
                if _is_terminal_status_event(runtime_event):
                    return

    def _project_runtime_event(
        self,
        runtime_event: object,
        *,
        thread_id: UUID,
        turn_id: UUID,
        trace_id: str,
    ) -> ConversationStreamEvent | None:
        event_type = getattr(runtime_event, "event_type", None)
        payload = getattr(runtime_event, "payload", {})
        if not isinstance(payload, dict):
            payload = {}
        if event_type is EventType.MESSAGE_CREATED:
            role = payload.get("role")
            if role == ThreadMessageRole.USER.value:
                kind = ConversationStreamEventKind.MESSAGE_CREATED
            elif role == ThreadMessageRole.ASSISTANT.value:
                kind = ConversationStreamEventKind.MESSAGE_COMPLETED
            else:
                kind = ConversationStreamEventKind.MESSAGE_DELTA
            return _event(
                kind,
                thread_id=thread_id,
                turn_id=turn_id,
                sequence=1,
                trace_id=trace_id,
                payload=payload,
            )
        if event_type is EventType.MODEL_CALL_CREATED:
            if payload.get("reasoning_started") is True:
                return _event(
                    ConversationStreamEventKind.REASONING_STARTED,
                    thread_id=thread_id,
                    turn_id=turn_id,
                    sequence=1,
                    trace_id=trace_id,
                    payload={},
                )
            reasoning_delta = payload.get("reasoning_delta")
            if isinstance(reasoning_delta, str) and reasoning_delta:
                return _event(
                    ConversationStreamEventKind.REASONING_DELTA,
                    thread_id=thread_id,
                    turn_id=turn_id,
                    sequence=1,
                    trace_id=trace_id,
                    payload={"text": reasoning_delta},
                )
            if "reasoning_completed" in payload:
                return _event(
                    ConversationStreamEventKind.REASONING_COMPLETED,
                    thread_id=thread_id,
                    turn_id=turn_id,
                    sequence=1,
                    trace_id=trace_id,
                    payload={"failed": bool(payload.get("reasoning_failed", False))},
                )
            usage = {
                key: payload[key]
                for key in (
                    "input_tokens",
                    "output_tokens",
                    "reasoning_tokens",
                    "cache_read_tokens",
                    "cache_write_tokens",
                )
                if key in payload
            }
            if usage:
                return _event(
                    ConversationStreamEventKind.USAGE_UPDATED,
                    thread_id=thread_id,
                    turn_id=turn_id,
                    sequence=1,
                    trace_id=trace_id,
                    payload=usage,
                )
            text_delta = payload.get("text_delta")
            if isinstance(text_delta, str) and text_delta:
                return _event(
                    ConversationStreamEventKind.MESSAGE_DELTA,
                    thread_id=thread_id,
                    turn_id=turn_id,
                    sequence=1,
                    trace_id=trace_id,
                    payload={
                        "text": text_delta,
                        "run_id": str(getattr(runtime_event, "run_id", "")),
                    },
                )
        if event_type is EventType.TOOL_CALL_CREATED:
            tool_name = payload.get("tool") or payload.get("name")
            return _event(
                ConversationStreamEventKind.MESSAGE_DELTA,
                thread_id=thread_id,
                turn_id=turn_id,
                sequence=1,
                trace_id=trace_id,
                payload={
                    "run_id": str(getattr(runtime_event, "run_id", "")),
                    "tool_event": {
                        "name": str(tool_name or "tool"),
                        "summary": str(payload.get("status") or "completed"),
                    },
                },
            )
        if event_type is EventType.RUN_STATUS_CHANGED:
            status = str(payload.get("status") or "")
            if status == RunStatus.COMPLETED.value:
                return _event(
                    ConversationStreamEventKind.TURN_COMPLETED,
                    thread_id=thread_id,
                    turn_id=turn_id,
                    sequence=1,
                    trace_id=trace_id,
                    payload={"status": status},
                )
            if status in {
                RunStatus.FAILED.value,
                RunStatus.CANCELLED.value,
                RunStatus.RECOVERY_REQUIRED.value,
            }:
                return _error_event(
                    thread_id=thread_id,
                    turn_id=turn_id,
                    sequence=1,
                    trace_id=trace_id,
                    error=ModelErrorInfo(
                        code=ModelErrorCode.PROVIDER_PROTOCOL,
                        message=str(payload.get("error") or status),
                        retryable=status == RunStatus.RECOVERY_REQUIRED.value,
                        provider="runtime",
                    ),
                )
        return None


def _event(
    kind: ConversationStreamEventKind,
    *,
    thread_id: UUID,
    turn_id: UUID,
    sequence: int,
    trace_id: str,
    payload: dict[str, object],
) -> ConversationStreamEvent:
    return ConversationStreamEvent(
        event=kind,
        thread_id=thread_id,
        turn_id=turn_id,
        sequence=sequence,
        trace_id=trace_id,
        payload=payload,
    )


def _is_resumable_run(run: Run) -> bool:
    if run.status in {
        RunStatus.COMPLETED,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
    }:
        return False
    return (
        run.status
        in {
            RunStatus.PAUSED,
            RunStatus.WAITING,
            RunStatus.RECOVERY_REQUIRED,
        }
        or run.dispatch_status is DispatchStatus.WAITING
    )


def _error_event(
    *,
    thread_id: UUID,
    turn_id: UUID,
    sequence: int,
    trace_id: str,
    error: ModelErrorInfo,
) -> ConversationStreamEvent:
    return _event(
        ConversationStreamEventKind.ERROR,
        thread_id=thread_id,
        turn_id=turn_id,
        sequence=sequence,
        trace_id=trace_id,
        payload=error.model_dump(mode="json"),
    )


def _is_terminal_status_event(runtime_event: object) -> bool:
    if getattr(runtime_event, "event_type", None) is not EventType.RUN_STATUS_CHANGED:
        return False
    payload = getattr(runtime_event, "payload", {})
    if not isinstance(payload, dict):
        return False
    return payload.get("status") in {
        RunStatus.COMPLETED.value,
        RunStatus.FAILED.value,
        RunStatus.CANCELLED.value,
        RunStatus.RECOVERY_REQUIRED.value,
    }
