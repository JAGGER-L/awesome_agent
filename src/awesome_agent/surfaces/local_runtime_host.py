from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from queue import Queue
from threading import Thread
from typing import cast
from uuid import UUID

from awesome_agent.conversation.events import (
    ConversationStreamEvent,
    ConversationStreamEventKind,
)
from awesome_agent.conversation.models import ThreadMessage
from awesome_agent.domain.enums import (
    ApprovalStatus,
    DispatchStatus,
    EventType,
    RunStatus,
)
from awesome_agent.domain.models import Run, RuntimeEvent
from awesome_agent.memory.models import MemoryTarget
from awesome_agent.modeling.provider import ModelProvider
from awesome_agent.persistence.approval_contracts import (
    ApprovalExpired,
    DurableApproval,
)
from awesome_agent.runtime.dispatch import DispatchConflict
from awesome_agent.settings import Settings
from awesome_agent.surfaces.capabilities import CapabilitySurfaceService
from awesome_agent.surfaces.client import (
    ChangedFileSummary,
    SurfaceThread,
    changed_file_summaries_from_payload,
)
from awesome_agent.surfaces.local_runtime_container import LocalRuntimeContainer


class LocalRuntimeHost:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        provider_factory: Callable[[str], ModelProvider] | None = None,
        default_model: str | None = None,
        repository: object | None = None,
        project_root: Path | None = None,
    ) -> None:
        self.settings = settings or Settings()
        self.default_model = default_model or self.settings.leader_model
        if repository is not None:
            raise ValueError(
                "LocalRuntimeHost no longer accepts an injected conversation-only "
                "repository; use LocalRuntimeContainer state_path for durable local "
                "runtime tests."
            )
        self._container = LocalRuntimeContainer(
            settings=self.settings,
            provider_factory=provider_factory,
            default_model=self.default_model,
            project_root=project_root,
        )
        self.repository = self._container.conversations
        self.runtime_repository = self._container.runtime
        self.event_stream = self._container.events
        self.tool_registry = self._container.tool_registry
        self.tool_executor = self._container.tool_executor
        self._conversation = self._container.conversation_service

    def close(self) -> None:
        self._container.close()

    def create_thread(self, title: str, **kwargs: object) -> SurfaceThread:
        requested_local = kwargs.get("local_memory_enabled")
        local_memory_enabled = (
            requested_local
            if isinstance(requested_local, bool)
            else self.settings.builtin_memory_enabled
        )
        return _run_async(
            self._create_thread_async(
                title,
                context_kind=_optional_str(kwargs.get("context_kind")) or "workspace",
                context_path=_optional_str(kwargs.get("context_path"))
                or str(Path.cwd()),
                default_model=_optional_str(kwargs.get("default_model")),
                sandbox_profile=_optional_str(kwargs.get("sandbox_profile")),
                thinking_mode=_optional_str(kwargs.get("thinking_mode")),
                local_memory_enabled=local_memory_enabled,
                provider_memory=_optional_str(kwargs.get("provider_memory"))
                or ("mem0" if self.settings.mem0_enabled else None),
            )
        )

    async def _create_thread_async(
        self,
        title: str,
        *,
        context_kind: str,
        context_path: str | None,
        default_model: str | None,
        sandbox_profile: str | None,
        thinking_mode: str | None,
        local_memory_enabled: bool,
        provider_memory: str | None,
    ) -> SurfaceThread:
        thread = await self.repository.create_thread(
            title=title,
            context_kind=context_kind,
            context_path=context_path,
            default_model=default_model,
            sandbox_profile=sandbox_profile,
            thinking_mode=thinking_mode,
            local_memory_enabled=local_memory_enabled,
            provider_memory=provider_memory,
        )
        return SurfaceThread(
            id=str(thread.id),
            title=thread.title,
            short_id=str(thread.id)[:8],
            context_label=thread.context_path,
            updated_label="now",
            default_model=thread.default_model,
            thinking_mode=thread.thinking_mode,
            local_memory_enabled=thread.local_memory_enabled,
            provider_memory=thread.provider_memory,
        )

    def list_threads(self) -> list[SurfaceThread]:
        return _run_async(self._list_threads_async())

    async def _list_threads_async(self) -> list[SurfaceThread]:
        threads = await self.repository.list_threads()
        summaries: list[SurfaceThread] = []
        for thread in threads:
            changed_files = _latest_changed_files(
                await self.repository.list_messages(thread.id)
            )
            summaries.append(
                SurfaceThread(
                    id=str(thread.id),
                    title=thread.title,
                    short_id=str(thread.id)[:8],
                    context_label=thread.context_path,
                    updated_label="now",
                    changed_file_count=len(changed_files),
                    latest_changed_files=changed_files,
                    default_model=thread.default_model,
                    thinking_mode=thread.thinking_mode,
                    local_memory_enabled=thread.local_memory_enabled,
                    provider_memory=thread.provider_memory,
                )
            )
        return summaries

    def resume_thread(self, query: str) -> SurfaceThread:
        return _run_async(self._resume_thread_async(query))

    async def _resume_thread_async(self, query: str) -> SurfaceThread:
        thread = await self.repository.resolve_thread(query)
        return SurfaceThread(
            id=str(thread.id),
            title=thread.title,
            short_id=str(thread.id)[:8],
            context_label=thread.context_path,
            updated_label="now",
            default_model=thread.default_model,
            thinking_mode=thread.thinking_mode,
            local_memory_enabled=thread.local_memory_enabled,
            provider_memory=thread.provider_memory,
        )

    def update_thread_settings(
        self,
        thread_id: str,
        *,
        default_model: str | None = None,
        thinking_mode: str | None = None,
        local_memory_enabled: bool | None = None,
        provider_memory: str | None = None,
    ) -> SurfaceThread:
        return _run_async(
            self._update_thread_settings_async(
                thread_id,
                default_model=default_model,
                thinking_mode=thinking_mode,
                local_memory_enabled=local_memory_enabled,
                provider_memory=provider_memory,
            )
        )

    async def _update_thread_settings_async(
        self,
        thread_id: str,
        *,
        default_model: str | None,
        thinking_mode: str | None,
        local_memory_enabled: bool | None,
        provider_memory: str | None,
    ) -> SurfaceThread:
        thread = await self.repository.update_thread_settings(
            UUID(thread_id),
            default_model=default_model,
            thinking_mode=thinking_mode,
            local_memory_enabled=local_memory_enabled,
            provider_memory=provider_memory,
        )
        return SurfaceThread(
            id=str(thread.id),
            title=thread.title,
            short_id=str(thread.id)[:8],
            context_label=thread.context_path,
            updated_label="now",
            default_model=thread.default_model,
            thinking_mode=thread.thinking_mode,
            local_memory_enabled=thread.local_memory_enabled,
            provider_memory=thread.provider_memory,
        )

    def list_thread_messages(self, thread_id: str) -> list[dict[str, object]]:
        return _run_async(self._list_thread_messages_async(thread_id))

    async def _list_thread_messages_async(
        self,
        thread_id: str,
    ) -> list[dict[str, object]]:
        messages = await self.repository.list_messages(UUID(thread_id))
        return [message.model_dump(mode="json") for message in messages]

    def last_resumable_run(self, thread_id: str) -> dict[str, object] | None:
        for run in self.list_thread_runs(thread_id):
            if run.get("status") in {
                "waiting",
                "paused",
                "recovery_required",
            }:
                return run
        return None

    def stream_turn(
        self,
        thread_id: str,
        content: str,
        *,
        model: str | None = None,
        thinking: str | None = None,
        memory: dict[str, object] | None = None,
        skill_ids: tuple[str, ...] = (),
    ) -> Iterable[ConversationStreamEvent]:
        yield from _iter_async_in_thread(
            self._stream_turn_async(
                thread_id=UUID(thread_id),
                content=content,
                model=model,
                thinking=thinking,
                memory=memory,
                skill_ids=skill_ids,
            )
        )

    def continue_turn(
        self,
        thread_id: str,
        *,
        expected_run_id: str | None = None,
        after_sequence: int = 0,
    ) -> Iterable[ConversationStreamEvent]:
        yield from _iter_async_in_thread(
            self._continue_turn_async(
                thread_id=UUID(thread_id),
                expected_run_id=UUID(expected_run_id)
                if expected_run_id is not None
                else None,
                after_sequence=after_sequence,
            )
        )

    async def _continue_turn_async(
        self,
        *,
        thread_id: UUID,
        expected_run_id: UUID | None,
        after_sequence: int,
    ) -> AsyncIterator[ConversationStreamEvent]:
        drained_run_ids: set[UUID] = set()
        async for event in self._conversation.continue_turn(
            thread_id=thread_id,
            expected_run_id=expected_run_id,
            after_sequence=after_sequence,
        ):
            yield event
            if event.event is not ConversationStreamEventKind.TURN_CONTINUED:
                continue
            run_id_value = event.payload.get("run_id")
            if not isinstance(run_id_value, str):
                continue
            run_id = UUID(run_id_value)
            if run_id in drained_run_ids:
                continue
            drained_run_ids.add(run_id)
            sequence = event.sequence
            async for projected in self._stream_run_with_worker(
                thread_id=thread_id,
                turn_id=event.turn_id,
                trace_id=event.trace_id,
                run_id=run_id,
                after_sequence=after_sequence,
            ):
                sequence += 1
                yield projected.model_copy(update={"sequence": sequence})
            return

    async def _stream_run_with_worker(
        self,
        *,
        thread_id: UUID,
        turn_id: UUID,
        trace_id: str,
        run_id: UUID,
        after_sequence: int,
    ) -> AsyncIterator[ConversationStreamEvent]:
        pump = asyncio.create_task(
            self._container.worker_pump.drain_until_run_terminal_or_waiting(str(run_id))
        )
        try:
            async for projected in self._stream_existing_run_events(
                thread_id=thread_id,
                turn_id=turn_id,
                trace_id=trace_id,
                run_id=run_id,
                after_sequence=after_sequence,
            ):
                yield projected
            await pump
        except BaseException:
            if not pump.done():
                pump.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await pump
            raise

    async def _stream_existing_run_events(
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
            runtime_events = await self.runtime_repository.list_events(
                run_id,
                after_sequence=last_sequence,
            )
            if runtime_events:
                for runtime_event in runtime_events:
                    last_sequence = max(last_sequence, runtime_event.sequence)
                    projected = self._conversation._project_runtime_event(
                        runtime_event,
                        thread_id=thread_id,
                        turn_id=turn_id,
                        trace_id=trace_id,
                    )
                    if projected is not None:
                        yield projected
                    if _is_stream_terminal(projected):
                        return
                continue
            try:
                run = await self.runtime_repository.get_run(run_id)
            except KeyError:
                return
            if run.status in {
                RunStatus.COMPLETED,
                RunStatus.FAILED,
                RunStatus.CANCELLED,
                RunStatus.RECOVERY_REQUIRED,
            }:
                return
            if run.dispatch_status is DispatchStatus.WAITING:
                return
            await asyncio.sleep(
                min(max(self.settings.event_poll_interval_seconds, 0.001), 0.01)
            )

    async def _project_current_run_events(
        self,
        *,
        thread_id: UUID,
        turn_id: UUID,
        trace_id: str,
        run_id: UUID,
    ) -> AsyncIterator[ConversationStreamEvent]:
        for runtime_event in await self.runtime_repository.list_events(run_id):
            projected = self._conversation._project_runtime_event(
                runtime_event,
                thread_id=thread_id,
                turn_id=turn_id,
                trace_id=trace_id,
            )
            if projected is not None:
                yield projected

    async def _stream_turn_async(
        self,
        *,
        thread_id: UUID,
        content: str,
        model: str | None,
        thinking: str | None,
        memory: dict[str, object] | None,
        skill_ids: tuple[str, ...],
    ) -> AsyncIterator[ConversationStreamEvent]:
        executed_run_ids: set[UUID] = set()
        async for event in self._conversation.start_turn(
            thread_id=thread_id,
            content=content,
            model=model,
            thinking=thinking,
            memory=memory,
            skill_ids=skill_ids,
        ):
            yield event
            if event.event is not ConversationStreamEventKind.TURN_STARTED:
                continue
            run_id_value = event.payload.get("run_id")
            if not isinstance(run_id_value, str):
                continue
            run_id = UUID(run_id_value)
            if run_id in executed_run_ids:
                continue
            executed_run_ids.add(run_id)
            async for projected in self._stream_run_with_worker(
                thread_id=thread_id,
                turn_id=event.turn_id,
                trace_id=event.trace_id,
                run_id=run_id,
                after_sequence=0,
            ):
                yield projected
            return

    def list_thread_runs(self, thread_id: str) -> list[dict[str, object]]:
        return _run_async(self._list_thread_runs_async(thread_id))

    async def _list_thread_runs_async(self, thread_id: str) -> list[dict[str, object]]:
        runs: list[tuple[RuntimeEvent, Run, dict[str, object]]] = []
        for run in await self.runtime_repository.list_runs():
            event = await self._run_created_event(run.id)
            if event is None:
                continue
            payload = event.payload
            if payload.get("thread_id") != thread_id:
                continue
            runs.append(
                (
                    event,
                    run,
                    {
                        "id": str(run.id),
                        "thread_id": thread_id,
                        "goal": run.goal,
                        "status": run.status.value,
                        "dispatch_status": run.dispatch_status.value,
                        "runtime_route": run.runtime_route,
                        "execution_kind": run.execution_kind.value,
                        "extension_catalog_version": run.extension_catalog_version,
                        "result_text": run.result_text,
                    },
                )
            )
        runs.sort(
            key=lambda item: (item[0].created_at, item[1].created_at, item[1].id.hex),
            reverse=True,
        )
        return [projection for _event, _run, projection in runs]

    def cancel(self, run_id: str) -> dict[str, object]:
        return _run_async(self._cancel_async(run_id))

    async def _cancel_async(self, run_id: str) -> dict[str, object]:
        run_uuid = UUID(run_id)
        event_sequence: int | None = None
        try:
            event = await self._container.dispatcher.request_cancellation(
                run_id=run_uuid,
                requested_by="local-surface",
                reason="user_requested",
            )
        except KeyError:
            return _cancel_run_not_found_response(run_id)
        except DispatchConflict:
            event = None
        if event is not None:
            event_sequence = event.sequence
        try:
            run = await self.runtime_repository.get_run(run_uuid)
        except KeyError:
            return _cancel_run_not_found_response(run_id)
        return {
            "run_id": run_id,
            "status": run.status.value,
            "dispatch_status": run.dispatch_status.value,
            "event_sequence": event_sequence,
        }

    def decide_approval(
        self,
        run_id: str,
        approval_id: str,
        *,
        approved: bool,
    ) -> dict[str, object]:
        return _run_async(
            self._decide_approval_async(
                run_id,
                approval_id,
                approved=approved,
            )
        )

    async def _decide_approval_async(
        self,
        run_id: str,
        approval_id: str,
        *,
        approved: bool,
    ) -> dict[str, object]:
        try:
            run_uuid = UUID(run_id)
            approval_uuid = UUID(approval_id)
        except ValueError:
            return _approval_not_found_response(
                run_id,
                approval_id,
                approved=approved,
            )
        try:
            run = await self.runtime_repository.get_run(run_uuid)
            approval = await self._container.approvals.get(approval_uuid)
        except KeyError:
            return _approval_not_found_response(
                run_id,
                approval_id,
                approved=approved,
            )
        if approval.run_id != run_uuid:
            return _approval_not_found_response(
                run_id,
                approval_id,
                approved=approved,
            )

        if approval.status is not ApprovalStatus.PENDING:
            return _approval_conflict_response(
                run_id,
                approval_id,
                approved=approved,
                status=approval.status.value,
                reason="approval_not_pending",
            )
        if not _is_waiting_for_approval(run):
            return _approval_conflict_response(
                run_id,
                approval_id,
                approved=approved,
                status=approval.status.value,
                reason="run_not_waiting_for_approval",
            )
        if not await self._container.dispatcher.is_waiting_for_approval(
            run_id=run_uuid,
            approval_id=approval_uuid,
        ):
            return _approval_conflict_response(
                run_id,
                approval_id,
                approved=approved,
                status=approval.status.value,
                reason="approval_not_current",
            )

        now = datetime.now(UTC)
        try:
            decided = await self._container.approvals.decide(
                approval_uuid,
                approved=approved,
                decided_by="local-surface",
                reason="approval_decided",
                now=now,
            )
        except ApprovalExpired as error:
            decided = error.approval
            await self._append_approval_decision_event(
                decided,
                approved=approved,
                reason="approval_expired",
            )
            await self._container.dispatcher.requeue_after_approval(
                run_id=run_uuid,
                approval_id=approval_uuid,
                reason="approval_expired",
            )
            return {
                "run_id": run_id,
                "approval_id": approval_id,
                "approved": approved,
                "status": ApprovalStatus.EXPIRED.value,
                "reason": "approval_expired",
            }

        await self._append_approval_decision_event(
            decided,
            approved=approved,
            reason="approval_decided",
        )
        await self._container.dispatcher.requeue_after_approval(
            run_id=run_uuid,
            approval_id=approval_uuid,
            reason="approval_decided",
        )
        await self._container.worker_pump.drain_until_run_terminal_or_waiting(run_id)
        return {
            "run_id": run_id,
            "approval_id": approval_id,
            "approved": approved,
            "status": decided.status.value,
            "reason": "approval_decided",
        }

    async def _append_approval_decision_event(
        self,
        approval: DurableApproval,
        *,
        approved: bool,
        reason: str,
    ) -> None:
        await self.runtime_repository.append_event(
            run_id=approval.run_id,
            event_type=EventType.APPROVAL_DECIDED,
            payload={
                "approval_id": str(approval.id),
                "approved": approved,
                "status": approval.status.value,
                "reason": reason,
            },
            transition_id=f"approval-decided:{approval.id}",
        )

    async def _run_created_event(self, run_id: UUID) -> RuntimeEvent | None:
        for event in await self.runtime_repository.list_events(run_id):
            if event.event_type.value == "run.created":
                return event
        return None

    def runtime_status(self) -> dict[str, object]:
        return {
            "runtime": "embedded",
            "transport": "local",
            "sandbox": self.settings.local_cli_sandbox_backend,
        }

    def list_models(self) -> list[dict[str, object]]:
        configured = self.settings.deepseek_api_key is not None
        return [
            {
                "name": self.settings.leader_model,
                "role": "leader",
                "provider": "deepseek",
                "configured": configured,
                "api_key_env": "AWESOME_AGENT_DEEPSEEK_API_KEY",
                "api_key_present": configured,
                "base_url": self.settings.deepseek_base_url,
                "source": "settings",
                "overridden_by_env": False,
            }
        ]

    def memory_summary(self) -> dict[str, object]:
        return self._container.memory_service.status().model_dump(mode="json")

    def memory_entries(self, target: str | None = None) -> list[dict[str, object]]:
        return _run_async(self._memory_entries_async(target))

    async def _memory_entries_async(
        self,
        target: str | None,
    ) -> list[dict[str, object]]:
        parsed = MemoryTarget(target) if target is not None else None
        result = await self._container.memory_service.list_entries(target=parsed)
        return [entry.model_dump(mode="json") for entry in result.entries]

    def delete_memory_entry(self, memory_id: str, *, target: str) -> dict[str, object]:
        return _run_async(self._delete_memory_entry_async(memory_id, target=target))

    async def _delete_memory_entry_async(
        self,
        memory_id: str,
        *,
        target: str,
    ) -> dict[str, object]:
        result = await self._container.memory_service.delete(
            target=MemoryTarget(target),
            memory_id=memory_id,
            run_id=None,
            agent_id=None,
        )
        return result.model_dump(mode="json", exclude_none=True)

    def list_skills(self) -> list[dict[str, object]]:
        catalog = self._container.extension_catalog_store.active()
        return CapabilitySurfaceService(
            catalog=catalog,
            tool_registry=self.tool_registry,
        ).skills()

    def mcp_status(self) -> list[dict[str, object]]:
        catalog = self._container.extension_catalog_store.active()
        return CapabilitySurfaceService(
            catalog=catalog,
            tool_registry=self.tool_registry,
        ).mcp_servers()

    def list_tools(self) -> dict[str, list[dict[str, object]]]:
        catalog = self._container.extension_catalog_store.active()
        return CapabilitySurfaceService(
            catalog=catalog,
            tool_registry=self.tool_registry,
        ).tools()

    def usage_summary(
        self,
        thread_id: str | None,
        run_id: str | None,
    ) -> dict[str, object]:
        if thread_id is None:
            return {
                "thread_id": None,
                "run_id": run_id,
                "input_tokens": 0,
                "output_tokens": 0,
                "reasoning_tokens": 0,
                "total_tokens": 0,
                "budget": "-",
            }
        return _run_async(self._usage_summary_async(thread_id, run_id))

    async def _usage_summary_async(
        self,
        thread_id: str,
        run_id: str | None,
    ) -> dict[str, object]:
        input_tokens = 0
        output_tokens = 0
        reasoning_tokens = 0
        for message in await self.repository.list_messages(UUID(thread_id)):
            usage = message.metadata.get("usage")
            if not isinstance(usage, dict):
                continue
            if run_id is not None and str(message.run_id) != run_id:
                continue
            input_tokens += _int_usage(usage.get("input_tokens"))
            output_tokens += _int_usage(usage.get("output_tokens"))
            reasoning_tokens += _int_usage(usage.get("reasoning_tokens"))
        return {
            "thread_id": thread_id,
            "run_id": run_id,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "reasoning_tokens": reasoning_tokens,
            "total_tokens": input_tokens + output_tokens,
            "budget": "-",
        }

    def config_summary(self) -> dict[str, object]:
        return {
            "mode": "embedded",
            "sandbox_backend": self.settings.local_cli_sandbox_backend,
            "default_model": self.settings.leader_model,
            "deepseek_api_key_configured": self.settings.deepseek_api_key is not None,
        }


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _cancel_run_not_found_response(run_id: str) -> dict[str, object]:
    return {
        "run_id": run_id,
        "status": "not_found",
        "reason": "run_not_found",
        "dispatch_status": None,
        "event_sequence": None,
    }


def _approval_not_found_response(
    run_id: str,
    approval_id: str,
    *,
    approved: bool,
) -> dict[str, object]:
    return {
        "run_id": run_id,
        "approval_id": approval_id,
        "approved": approved,
        "status": "not_found",
        "reason": "approval_not_found",
    }


def _approval_conflict_response(
    run_id: str,
    approval_id: str,
    *,
    approved: bool,
    status: str,
    reason: str,
) -> dict[str, object]:
    return {
        "run_id": run_id,
        "approval_id": approval_id,
        "approved": approved,
        "status": status,
        "reason": reason,
    }


def _is_waiting_for_approval(run: Run) -> bool:
    return run.dispatch_status is DispatchStatus.WAITING and run.status in {
        RunStatus.PAUSED,
        RunStatus.WAITING,
    }


def _is_stream_terminal(event: ConversationStreamEvent | None) -> bool:
    if event is None:
        return False
    return event.event in {
        ConversationStreamEventKind.TURN_COMPLETED,
        ConversationStreamEventKind.ERROR,
    }


def _latest_changed_files(
    messages: list[ThreadMessage],
) -> tuple[ChangedFileSummary, ...]:
    for message in reversed(messages):
        changed_files = changed_file_summaries_from_payload(
            message.metadata.get("changed_files")
        )
        if changed_files:
            return changed_files
    return ()


def _int_usage(value: object) -> int:
    return value if isinstance(value, int) else 0


def _run_async[T](awaitable: Awaitable[T]) -> T:
    sentinel = object()
    queue: Queue[object] = Queue()

    async def collect() -> None:
        try:
            queue.put(await awaitable)
        except BaseException as error:
            queue.put(error)
        finally:
            queue.put(sentinel)

    def runner() -> None:
        asyncio.run(collect())

    thread = Thread(target=runner, daemon=True)
    thread.start()
    item = queue.get()
    thread.join()
    sentinel_item = queue.get()
    if sentinel_item is not sentinel:
        raise RuntimeError("Local runtime host async bridge ended unexpectedly.")
    if isinstance(item, BaseException):
        raise item
    return cast(T, item)


def _iter_async_in_thread[T](
    iterator: AsyncIterator[T],
) -> Iterable[T]:
    sentinel = object()
    queue: Queue[object] = Queue()

    async def collect() -> None:
        try:
            async for item in iterator:
                queue.put(item)
        except BaseException as error:
            queue.put(error)
        finally:
            queue.put(sentinel)

    def runner() -> None:
        asyncio.run(collect())

    thread = Thread(target=runner, daemon=True)
    thread.start()
    while True:
        item = queue.get()
        if item is sentinel:
            break
        if isinstance(item, BaseException):
            raise item
        yield item  # type: ignore[misc]
    thread.join()
