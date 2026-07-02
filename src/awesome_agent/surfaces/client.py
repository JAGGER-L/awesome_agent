from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Protocol

from awesome_agent.conversation.events import ConversationStreamEvent


@dataclass(frozen=True)
class SurfaceThread:
    id: str
    title: str
    short_id: str
    context_label: str | None = None


@dataclass(frozen=True)
class SurfaceRun:
    id: str
    status: str
    goal: str
    execution_mode: str | None = None


class SurfaceClientError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "surface_client_error",
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class SurfaceClient(Protocol):
    def close(self) -> None: ...

    def create_thread(self, title: str, **kwargs: object) -> SurfaceThread: ...

    def list_threads(self) -> list[SurfaceThread]: ...

    def resume_thread(self, query: str) -> SurfaceThread: ...

    def list_thread_messages(self, thread_id: str) -> list[dict[str, Any]]: ...

    def stream_turn(
        self,
        thread_id: str,
        content: str,
        *,
        model: str | None = None,
        resume_run_id: str | None = None,
    ) -> Iterable[ConversationStreamEvent]: ...

    def start_explicit_run(
        self,
        thread_id: str,
        goal: str,
        **kwargs: object,
    ) -> dict[str, Any]: ...

    def runtime_status(self) -> dict[str, object]: ...

    def list_models(self) -> list[dict[str, Any]]: ...

    def memory_summary(self) -> dict[str, object]: ...

    def list_skills(self) -> list[dict[str, Any]]: ...

    def list_tools(self) -> dict[str, list[dict[str, Any]]]: ...

    def mcp_status(self) -> list[dict[str, Any]]: ...

    def list_uploads(self, thread_id: str | None) -> list[dict[str, Any]]: ...

    def list_current_artifacts(
        self,
        thread_id: str | None,
        run_id: str | None,
    ) -> list[dict[str, Any]]: ...

    def usage_summary(
        self,
        thread_id: str | None,
        run_id: str | None,
    ) -> dict[str, object]: ...

    def config_summary(self) -> dict[str, object]: ...

    def cancel(self, run_id: str) -> dict[str, Any]: ...


def surface_thread_from_mapping(payload: dict[str, object]) -> SurfaceThread:
    thread_id = str(payload["id"])
    title = str(payload.get("title") or "New conversation")
    context_label = payload.get("context_path") or payload.get("context_label")
    return SurfaceThread(
        id=thread_id,
        title=title,
        short_id=thread_id[:8],
        context_label=str(context_label) if context_label is not None else None,
    )
