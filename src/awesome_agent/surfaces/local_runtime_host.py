from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator, Callable, Iterable
from enum import StrEnum
from queue import Queue
from threading import Thread
from uuid import UUID, uuid4

from awesome_agent.conversation.events import ConversationStreamEvent
from awesome_agent.conversation.models import ThreadMessage
from awesome_agent.conversation.runtime_turns import ProviderLeaderTurnExecutor
from awesome_agent.conversation.service import ConversationService
from awesome_agent.domain.enums import ExecutionOrigin
from awesome_agent.modeling.provider import ModelProvider
from awesome_agent.persistence.conversations import InMemoryConversationRepository
from awesome_agent.persistence.local_conversations import LocalConversationRepository
from awesome_agent.providers.factory import ModelProviderFactory
from awesome_agent.runtime.repository import InMemoryRuntimeRepository
from awesome_agent.sandbox.factory import create_sandbox
from awesome_agent.settings import Settings
from awesome_agent.surfaces.client import (
    ChangedFileSummary,
    SurfaceClientError,
    SurfaceThread,
    changed_file_summaries_from_payload,
)
from awesome_agent.tools.repository import (
    build_modifying_executor,
    build_modifying_registry,
)


class ExecutionMode(StrEnum):
    LEADER = "leader"
    CODING = "coding"
    RESUME = "resume"


def plan_execution_mode(
    content: str,
    *,
    resumable_run_id: str | None = None,
) -> ExecutionMode:
    normalized = content.strip().casefold()
    if resumable_run_id is not None and normalized == "\u7ee7\u7eed":
        return ExecutionMode.RESUME
    if resumable_run_id is not None and normalized in {
        "continue",
        "resume",
        "继续",
    }:
        return ExecutionMode.RESUME
    coding_markers = (
        "build",
        "create",
        "edit",
        "fix",
        "test",
        "html",
        "file",
        "code",
        "生成",
        "修改",
        "修复",
    )
    if any(marker in normalized for marker in coding_markers):
        return ExecutionMode.CODING
    return ExecutionMode.LEADER


class LocalRuntimeHost:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        provider_factory: Callable[[str], ModelProvider] | None = None,
        default_model: str | None = None,
        repository: (
            InMemoryConversationRepository | LocalConversationRepository | None
        ) = None,
    ) -> None:
        self.settings = settings or Settings()
        self.repository = repository or LocalConversationRepository(
            self.settings.local_state_dir / "awesome-agent.db"
        )
        self.runtime_repository = InMemoryRuntimeRepository()
        self.default_model = default_model or self.settings.leader_model
        self.tool_registry = build_modifying_registry(
            sandbox=create_sandbox(
                origin=ExecutionOrigin.CLI,
                settings=self.settings,
                profile="local-cli",
            )
        )
        self.tool_executor = build_modifying_executor(self.tool_registry)
        factory = provider_factory
        if factory is None:
            factory = ModelProviderFactory(self.settings).create
        self._conversation = ConversationService(
            repository=self.repository,
            runtime_repository=self.runtime_repository,
            leader_executor=ProviderLeaderTurnExecutor(factory),
            default_model=self.default_model,
            tool_executor=self.tool_executor,
            tool_registry=self.tool_registry,
        )
        self._planned_runs: dict[str, dict[str, object]] = {}

    def close(self) -> None:
        close = getattr(self.repository, "close", None)
        if callable(close):
            close()

    def create_thread(self, title: str, **kwargs: object) -> SurfaceThread:
        return _run_async(
            self._create_thread_async(
                title,
                context_kind=_optional_str(kwargs.get("context_kind")) or "workspace",
                context_path=_optional_str(kwargs.get("context_path")),
                default_model=_optional_str(kwargs.get("default_model")),
                sandbox_profile=_optional_str(kwargs.get("sandbox_profile")),
                thinking_mode=_optional_str(kwargs.get("thinking_mode")),
                local_memory_enabled=bool(kwargs.get("local_memory_enabled") or False),
                provider_memory=_optional_str(kwargs.get("provider_memory")),
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
        for run in reversed(list(self._planned_runs.values())):
            if run.get("thread_id") == thread_id and run.get("status") in {
                "cancelled",
                "interrupted",
                "paused",
            }:
                return dict(run)
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
        resume_run_id: str | None = None,
    ) -> Iterable[ConversationStreamEvent]:
        normalized = content.strip().casefold()
        if resume_run_id is not None and normalized in {"continue", "resume", "继续"}:
            raise SurfaceClientError(
                "Durable turn resume is not available yet.",
                code="resume_not_available",
            )
        yield from _iter_async_in_thread(
            self._conversation.start_turn(
                thread_id=UUID(thread_id),
                content=content,
                model=model,
                thinking=thinking,
                memory=memory,
                skill_ids=skill_ids,
            )
        )

    def start_explicit_run(
        self,
        thread_id: str,
        goal: str,
        **kwargs: object,
    ) -> dict[str, object]:
        mode = plan_execution_mode(goal)
        run_id = str(uuid4())
        payload: dict[str, object] = {
            "id": run_id,
            "thread_id": thread_id,
            "goal": goal,
            "status": "planned",
            "execution_mode": mode.value,
            "transport": "embedded",
        }
        self._planned_runs[run_id] = payload
        return payload

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
        return {
            "enabled": self.settings.builtin_memory_enabled
            or self.settings.mem0_enabled,
            "builtin": self.settings.builtin_memory_enabled,
            "mem0": self.settings.mem0_enabled,
        }

    def local_memory_facts(self, thread_id: str | None) -> list[str]:
        if thread_id is None:
            return []
        return _run_async(self._local_memory_facts_async(thread_id))

    async def _local_memory_facts_async(self, thread_id: str) -> list[str]:
        facts: list[str] = []
        seen: set[str] = set()
        for message in await self.repository.list_messages(UUID(thread_id)):
            if not _message_local_memory_enabled(message):
                continue
            fact = _extract_local_memory_fact(message.content)
            if fact is None or fact in seen:
                continue
            facts.append(fact)
            seen.add(fact)
        return facts

    def list_tools(self) -> dict[str, list[dict[str, object]]]:
        groups: dict[str, list[dict[str, object]]] = {
            "builtin": [],
            "sandbox": [],
            "mcp": [],
            "extension": [],
        }
        for spec in self.tool_registry.list_specs():
            item = {
                "name": spec.name,
                "risk_level": spec.risk_level.value,
                "health": "healthy",
                "description": spec.description,
            }
            if spec.sandbox_required:
                groups["sandbox"].append(item)
            else:
                groups["builtin"].append(item)
        return groups

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


def _message_local_memory_enabled(message: ThreadMessage) -> bool:
    options = message.metadata.get("turn_options")
    if not isinstance(options, dict):
        return False
    memory = options.get("memory")
    return isinstance(memory, dict) and memory.get("local_enabled") is True


def _extract_local_memory_fact(content: str) -> str | None:
    normalized = content.strip()
    for pattern in (
        r"^\u6211\u76ee\u524d\u5728\u5b66\u4e60(.+)$",
        r"^\u6211\u5728\u5b66\u4e60(.+)$",
    ):
        match = re.match(pattern, normalized, flags=re.IGNORECASE)
        if match:
            topic = match.group(1).strip(" \t\u3002.!?\uff1f")
            if topic:
                return f"\u7528\u6237\u76ee\u524d\u5728\u5b66\u4e60{topic}\u3002"
    english = re.match(
        r"^(?:i am|i'm|im) (?:currently )?learning (.+)$",
        normalized,
        flags=re.IGNORECASE,
    )
    if english:
        topic = english.group(1).strip(" \t.")
        if topic:
            return f"User is currently learning {topic}."
    return None


def _int_usage(value: object) -> int:
    return value if isinstance(value, int) else 0


def _run_async[T](awaitable: object) -> T:
    sentinel = object()
    queue: Queue[object] = Queue()

    async def collect() -> None:
        try:
            queue.put(await awaitable)  # type: ignore[misc]
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
    return item  # type: ignore[return-value]


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
