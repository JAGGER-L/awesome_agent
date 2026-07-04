from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
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

    def create_attachment(self, thread_id: str, path: Path) -> dict[str, Any]:
        return dict(self.host.create_attachment(thread_id, path))

    def list_attachments(
        self,
        thread_id: str,
        *,
        include_deleted: bool = False,
    ) -> list[dict[str, Any]]:
        return [
            dict(item)
            for item in self.host.list_attachments(
                thread_id,
                include_deleted=include_deleted,
            )
        ]

    def delete_attachment(self, thread_id: str, attachment_id: str) -> dict[str, Any]:
        return dict(self.host.delete_attachment(thread_id, attachment_id))

    def last_resumable_run(self, thread_id: str) -> dict[str, Any] | None:
        if hasattr(self.host, "last_resumable_run"):
            result = self.host.last_resumable_run(thread_id)
            return dict(result) if result is not None else None
        return None

    def update_thread_settings(
        self,
        thread_id: str,
        *,
        default_model: str | None = None,
        thinking_mode: str | None = None,
        local_memory_enabled: bool | None = None,
        provider_memory: str | None = None,
    ) -> SurfaceThread:
        return self.host.update_thread_settings(
            thread_id,
            default_model=default_model,
            thinking_mode=thinking_mode,
            local_memory_enabled=local_memory_enabled,
            provider_memory=provider_memory,
        )

    def stream_turn(
        self,
        thread_id: str,
        content: str,
        *,
        model: str | None = None,
        thinking: str | None = None,
        memory: dict[str, object] | None = None,
        skill_ids: tuple[str, ...] = (),
        attachment_ids: tuple[str, ...] = (),
    ) -> Iterable[ConversationStreamEvent]:
        return self.host.stream_turn(
            thread_id,
            content,
            model=model,
            thinking=thinking,
            memory=memory,
            skill_ids=skill_ids,
            attachment_ids=attachment_ids,
        )

    def continue_turn(
        self,
        thread_id: str,
        *,
        expected_run_id: str | None = None,
        after_sequence: int = 0,
    ) -> Iterable[ConversationStreamEvent]:
        return self.host.continue_turn(
            thread_id,
            expected_run_id=expected_run_id,
            after_sequence=after_sequence,
        )

    def list_thread_runs(self, thread_id: str) -> list[dict[str, Any]]:
        return [dict(item) for item in self.host.list_thread_runs(thread_id)]

    def runtime_status(self) -> dict[str, object]:
        return self.host.runtime_status()

    def list_models(self) -> dict[str, Any]:
        return dict(self.host.list_models())

    def memory_summary(self) -> dict[str, object]:
        return self.host.memory_summary()

    def memory_entries(self, target: str | None = None) -> list[dict[str, Any]]:
        return [dict(item) for item in self.host.memory_entries(target)]

    def delete_memory_entry(self, memory_id: str, *, target: str) -> dict[str, Any]:
        return dict(self.host.delete_memory_entry(memory_id, target=target))

    def list_skills(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self.host.list_skills()]

    def list_tools(self) -> dict[str, list[dict[str, Any]]]:
        return self.host.list_tools()

    def mcp_status(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self.host.mcp_status()]

    def usage_summary(
        self,
        thread_id: str | None,
        run_id: str | None,
    ) -> dict[str, object]:
        return self.host.usage_summary(thread_id, run_id)

    def config_summary(self) -> dict[str, object]:
        return self.host.config_summary()

    def cancel(
        self,
        run_id: str,
        *,
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        return dict(self.host.cancel(run_id))

    def decide_approval(
        self,
        run_id: str,
        approval_id: str,
        *,
        approved: bool,
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        return dict(
            self.host.decide_approval(
                run_id,
                approval_id,
                approved=approved,
            )
        )
