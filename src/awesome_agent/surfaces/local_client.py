from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from awesome_agent.conversation.events import ConversationStreamEvent
from awesome_agent.surfaces.client import SurfaceThread
from awesome_agent.surfaces.local_runtime_host import LocalRuntimeHost


class LocalSurfaceClient:
    def __init__(self, *, host: LocalRuntimeHost | None = None) -> None:
        self.host = host or LocalRuntimeHost()

    def close(self) -> None:
        self.host.close()

    def create_thread(self, title: str, **kwargs: object) -> SurfaceThread:
        return self.host.create_thread(title, **kwargs)

    def list_threads(self) -> list[SurfaceThread]:
        return self.host.list_threads()

    def resume_thread(self, query: str) -> SurfaceThread:
        return self.host.resume_thread(query)

    def list_thread_messages(self, thread_id: str) -> list[dict[str, Any]]:
        return self.host.list_thread_messages(thread_id)

    def last_resumable_run(self, thread_id: str) -> dict[str, Any] | None:
        if hasattr(self.host, "last_resumable_run"):
            result = self.host.last_resumable_run(thread_id)
            return dict(result) if result is not None else None
        return None

    def stream_turn(
        self,
        thread_id: str,
        content: str,
        *,
        model: str | None = None,
        resume_run_id: str | None = None,
    ) -> Iterable[ConversationStreamEvent]:
        return self.host.stream_turn(
            thread_id,
            content,
            model=model,
            resume_run_id=resume_run_id,
        )

    def start_explicit_run(
        self,
        thread_id: str,
        goal: str,
        **kwargs: object,
    ) -> dict[str, Any]:
        return dict(self.host.start_explicit_run(thread_id, goal, **kwargs))

    def runtime_status(self) -> dict[str, object]:
        return self.host.runtime_status()

    def list_models(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self.host.list_models()]

    def memory_summary(self) -> dict[str, object]:
        return self.host.memory_summary()

    def list_skills(self) -> list[dict[str, Any]]:
        return []

    def list_tools(self) -> dict[str, list[dict[str, Any]]]:
        return {"builtin": [], "sandbox": [], "mcp": [], "extension": []}

    def mcp_status(self) -> list[dict[str, Any]]:
        return []

    def list_uploads(self, thread_id: str | None) -> list[dict[str, Any]]:
        return []

    def list_current_artifacts(
        self,
        thread_id: str | None,
        run_id: str | None,
    ) -> list[dict[str, Any]]:
        return []

    def usage_summary(
        self,
        thread_id: str | None,
        run_id: str | None,
    ) -> dict[str, object]:
        return {"thread_id": thread_id, "run_id": run_id, "total_tokens": 0}

    def config_summary(self) -> dict[str, object]:
        return self.host.config_summary()

    def cancel(self, run_id: str) -> dict[str, Any]:
        return {"id": run_id, "status": "cancelled", "transport": "embedded"}
