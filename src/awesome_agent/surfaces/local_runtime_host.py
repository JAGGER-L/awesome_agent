from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Iterable
from enum import StrEnum
from queue import Queue
from threading import Thread
from uuid import UUID, uuid4

from awesome_agent.conversation.events import ConversationStreamEvent
from awesome_agent.conversation.service import ConversationService
from awesome_agent.modeling.provider import ModelProvider
from awesome_agent.persistence.conversations import InMemoryConversationRepository
from awesome_agent.providers.factory import ModelProviderFactory
from awesome_agent.settings import Settings
from awesome_agent.surfaces.client import SurfaceClientError, SurfaceThread


class ExecutionMode(StrEnum):
    LIGHTWEIGHT = "lightweight"
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
        "/resume",
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
    return ExecutionMode.LIGHTWEIGHT


class LocalRuntimeHost:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        provider_factory: Callable[[str], ModelProvider] | None = None,
        default_model: str | None = None,
    ) -> None:
        self.settings = settings or Settings()
        self.repository = InMemoryConversationRepository()
        self.default_model = default_model or self.settings.leader_model
        factory = provider_factory
        if factory is None:
            factory = ModelProviderFactory(self.settings).create
        self._conversation = ConversationService(
            repository=self.repository,
            provider_factory=factory,
            default_model=self.default_model,
        )
        self._planned_runs: dict[str, dict[str, object]] = {}

    def close(self) -> None:
        pass

    def create_thread(self, title: str, **kwargs: object) -> SurfaceThread:
        return _run_async(
            self._create_thread_async(
                title,
                context_kind=_optional_str(kwargs.get("context_kind")) or "workspace",
                context_path=_optional_str(kwargs.get("context_path")),
                default_model=_optional_str(kwargs.get("default_model")),
                sandbox_profile=_optional_str(kwargs.get("sandbox_profile")),
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
    ) -> SurfaceThread:
        thread = await self.repository.create_thread(
            title=title,
            context_kind=context_kind,
            context_path=context_path,
            default_model=default_model,
            sandbox_profile=sandbox_profile,
        )
        return SurfaceThread(
            id=str(thread.id),
            title=thread.title,
            short_id=str(thread.id)[:8],
            context_label=thread.context_path,
        )

    def list_threads(self) -> list[SurfaceThread]:
        return _run_async(self._list_threads_async())

    async def _list_threads_async(self) -> list[SurfaceThread]:
        threads = await self.repository.list_threads()
        return [
            SurfaceThread(
                id=str(thread.id),
                title=thread.title,
                short_id=str(thread.id)[:8],
                context_label=thread.context_path,
            )
            for thread in threads
        ]

    def resume_thread(self, query: str) -> SurfaceThread:
        return _run_async(self._resume_thread_async(query))

    async def _resume_thread_async(self, query: str) -> SurfaceThread:
        thread = await self.repository.resolve_thread(query)
        return SurfaceThread(
            id=str(thread.id),
            title=thread.title,
            short_id=str(thread.id)[:8],
            context_label=thread.context_path,
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
        resume_run_id: str | None = None,
    ) -> Iterable[ConversationStreamEvent]:
        mode = plan_execution_mode(content, resumable_run_id=resume_run_id)
        if mode is ExecutionMode.RESUME and resume_run_id is None:
            raise SurfaceClientError(
                "No resumable Run is available in the current thread.",
                code="no_resumable_run",
            )
        yield from _iter_async_in_thread(
            self._conversation.start_turn(
                thread_id=UUID(thread_id),
                content=content,
                model=model,
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

    def config_summary(self) -> dict[str, object]:
        return {
            "mode": "embedded",
            "sandbox_backend": self.settings.local_cli_sandbox_backend,
            "default_model": self.settings.leader_model,
            "deepseek_api_key_configured": self.settings.deepseek_api_key is not None,
        }


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


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
