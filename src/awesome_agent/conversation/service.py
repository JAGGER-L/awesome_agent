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
from awesome_agent.conversation.runtime_turns import project_runtime_event
from awesome_agent.domain.enums import (
    DispatchStatus,
    EventType,
    RunStatus,
)
from awesome_agent.domain.models import Run, RuntimeEvent
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
        global_builtin_memory_enabled: bool = False,
        global_provider_memory_enabled: bool = False,
    ) -> None:
        self._repository = repository
        self._runtime_repository = runtime_repository
        self._conversation_run_intake = conversation_run_intake
        self._default_model = default_model
        self._event_poll_interval = event_poll_interval
        self._global_builtin_memory_enabled = global_builtin_memory_enabled
        self._global_provider_memory_enabled = global_provider_memory_enabled

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
        thread = await self._repository.get_thread(thread_id)
        effective_memory = _effective_memory_payload(
            requested=memory or {},
            thread_local_enabled=thread.local_memory_enabled,
            thread_provider=thread.provider_memory,
            global_builtin_enabled=self._global_builtin_memory_enabled,
            global_provider_enabled=self._global_provider_memory_enabled,
        )
        run = await self._conversation_run_intake.create_turn_run(
            thread_id=thread_id,
            content=content,
            model=model,
            thinking=thinking,
            memory=effective_memory,
            skill_ids=skill_ids,
        )
        yield _event(
            ConversationStreamEventKind.TURN_STARTED,
            thread_id=thread_id,
            turn_id=turn_id,
            sequence=sequence,
            trace_id=trace_id,
            run_id=run.id,
            payload={
                "run_id": str(run.id),
                "status": run.status.value,
                "model": model or self._default_model,
                "memory": effective_memory,
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
        after_sequence: int = 0,
    ) -> AsyncIterator[ConversationStreamEvent]:
        await self._repository.get_thread(thread_id)
        run = await self.continuable_thread_run(
            thread_id,
            expected_run_id=expected_run_id,
        )
        if run is None:
            raise ValueError("no_resumable_turn")

        stream_id = uuid4()
        trace_id = uuid4().hex
        sequence = 1
        yield _event(
            ConversationStreamEventKind.TURN_CONTINUED,
            thread_id=thread_id,
            turn_id=stream_id,
            sequence=sequence,
            trace_id=trace_id,
            run_id=run.id,
            payload={
                "run_id": str(run.id),
                "stream_id": str(stream_id),
                "status": run.status.value,
                "dispatch_status": run.dispatch_status.value,
                "resumed": True,
                "after_sequence": after_sequence,
            },
        )
        async for projected in self._project_run_events(
            thread_id=thread_id,
            turn_id=stream_id,
            trace_id=trace_id,
            run_id=run.id,
            after_sequence=after_sequence,
        ):
            sequence += 1
            yield projected.model_copy(update={"sequence": sequence})

    async def continuable_thread_run(
        self,
        thread_id: UUID,
        *,
        expected_run_id: UUID | None = None,
    ) -> Run | None:
        await self._repository.get_thread(thread_id)
        if expected_run_id is not None:
            return await self._thread_run_by_id(thread_id, expected_run_id)
        return await self.latest_resumable_thread_run(thread_id)

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

    async def _thread_run_by_id(self, thread_id: UUID, run_id: UUID) -> Run | None:
        try:
            run = await self._runtime_repository.get_run(run_id)
        except KeyError:
            return None
        created_event = await self._run_created_event(run.id)
        if created_event is None:
            return None
        if created_event.payload.get("thread_id") != str(thread_id):
            return None
        return run

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
                        "extension_catalog_version": run.extension_catalog_version,
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
                try:
                    run = await self._runtime_repository.get_run(run_id)
                except KeyError:
                    return
                if run.status in {
                    RunStatus.COMPLETED,
                    RunStatus.FAILED,
                    RunStatus.CANCELLED,
                    RunStatus.RECOVERY_REQUIRED,
                }:
                    return
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
        if event_type is EventType.RUN_CREATED:
            return None
        if event_type is EventType.MESSAGE_CREATED:
            payload = getattr(runtime_event, "payload", {})
            if not isinstance(payload, dict):
                payload = {}
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
                run_id=getattr(runtime_event, "run_id", None),
                runtime_sequence=getattr(runtime_event, "sequence", None),
                payload=payload,
            )
        if isinstance(runtime_event, RuntimeEvent):
            projected = project_runtime_event(
                thread_id=thread_id,
                turn_id=turn_id,
                event=runtime_event,
            )
            if projected:
                return projected[0].model_copy(update={"trace_id": trace_id})
        return None


def _event(
    kind: ConversationStreamEventKind,
    *,
    thread_id: UUID,
    turn_id: UUID,
    sequence: int,
    trace_id: str,
    run_id: UUID | None = None,
    runtime_sequence: int | None = None,
    payload: dict[str, object],
) -> ConversationStreamEvent:
    return ConversationStreamEvent(
        event=kind,
        thread_id=thread_id,
        turn_id=turn_id,
        sequence=sequence,
        trace_id=trace_id,
        run_id=run_id,
        runtime_sequence=runtime_sequence,
        payload=payload,
    )


def _effective_memory_payload(
    *,
    requested: dict[str, object],
    thread_local_enabled: bool,
    thread_provider: str | None,
    global_builtin_enabled: bool,
    global_provider_enabled: bool,
) -> dict[str, object]:
    requested_local = requested.get("local_enabled")
    local_enabled = (
        requested_local if isinstance(requested_local, bool) else thread_local_enabled
    )
    provider_value = requested.get("provider")
    provider = provider_value if isinstance(provider_value, str) else thread_provider
    return {
        "local_enabled": bool(global_builtin_enabled and local_enabled),
        "provider": provider if global_provider_enabled else None,
    }


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
