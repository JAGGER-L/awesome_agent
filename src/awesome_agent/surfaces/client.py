from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from awesome_agent.conversation.events import ConversationStreamEvent


@dataclass(frozen=True)
class ChangedFileSummary:
    path: str
    status: str = "updated"
    display_path: str | None = None

    @property
    def visible_path(self) -> str:
        return self.display_path or self.path


@dataclass(frozen=True)
class SurfaceThread:
    id: str
    title: str
    short_id: str
    context_label: str | None = None
    updated_label: str | None = None
    changed_file_count: int = 0
    latest_changed_files: tuple[ChangedFileSummary, ...] = ()
    default_model: str | None = None
    thinking_mode: str | None = None
    local_memory_enabled: bool = False
    provider_memory: str | None = None


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

    def create_thread(
        self,
        title: str,
        *,
        context_kind: str | None = None,
        context_path: str | None = None,
        repository_id: str | None = None,
        default_model: str | None = None,
        sandbox_profile: str | None = None,
        thinking_mode: str | None = None,
        local_memory_enabled: bool = False,
        provider_memory: str | None = None,
        **kwargs: object,
    ) -> SurfaceThread | dict[str, object]: ...

    def list_threads(self) -> Sequence[SurfaceThread | dict[str, object]]: ...

    def resume_thread(self, query: str) -> SurfaceThread | dict[str, object]: ...

    def list_thread_messages(self, thread_id: str) -> list[dict[str, Any]]: ...

    def create_attachment(
        self,
        thread_id: str,
        path: Path,
    ) -> dict[str, Any]: ...

    def list_attachments(
        self,
        thread_id: str,
        *,
        include_deleted: bool = False,
    ) -> list[dict[str, Any]]: ...

    def delete_attachment(
        self,
        thread_id: str,
        attachment_id: str,
    ) -> dict[str, Any]: ...

    def last_resumable_run(self, thread_id: str) -> dict[str, Any] | None: ...

    def update_thread_settings(
        self,
        thread_id: str,
        *,
        default_model: str | None = None,
        thinking_mode: str | None = None,
        local_memory_enabled: bool | None = None,
        provider_memory: str | None = None,
    ) -> SurfaceThread | dict[str, object]: ...

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
    ) -> Iterable[ConversationStreamEvent]: ...

    def continue_turn(
        self,
        thread_id: str,
        *,
        expected_run_id: str | None = None,
        after_sequence: int = 0,
    ) -> Iterable[ConversationStreamEvent]: ...

    def list_thread_runs(self, thread_id: str) -> list[dict[str, Any]]: ...

    def runtime_status(self) -> dict[str, object]: ...

    def list_models(self) -> list[dict[str, Any]]: ...

    def memory_summary(self) -> dict[str, object]: ...

    def memory_entries(self, target: str | None = None) -> list[dict[str, Any]]: ...

    def delete_memory_entry(
        self,
        memory_id: str,
        *,
        target: str,
    ) -> dict[str, Any]: ...

    def list_skills(self) -> list[dict[str, Any]]: ...

    def list_tools(self) -> dict[str, list[dict[str, Any]]]: ...

    def mcp_status(self) -> list[dict[str, Any]]: ...

    def usage_summary(
        self,
        thread_id: str | None,
        run_id: str | None,
    ) -> dict[str, object]: ...

    def config_summary(self) -> dict[str, object]: ...

    def cancel(
        self,
        run_id: str,
        *,
        thread_id: str | None = None,
    ) -> dict[str, Any]: ...

    def decide_approval(
        self,
        run_id: str,
        approval_id: str,
        *,
        approved: bool,
        thread_id: str | None = None,
    ) -> dict[str, Any]: ...


def surface_thread_from_mapping(payload: dict[str, object]) -> SurfaceThread:
    thread_id = str(payload["id"])
    title = str(payload.get("title") or "New conversation")
    context_label = payload.get("context_path") or payload.get("context_label")
    latest_changed_files = tuple(
        changed_file_summary_from_mapping(item)
        for item in _mapping_list(payload.get("latest_changed_files"))
    )
    changed_file_count = payload.get("changed_file_count")
    return SurfaceThread(
        id=thread_id,
        title=title,
        short_id=thread_id[:8],
        context_label=str(context_label) if context_label is not None else None,
        updated_label=_relative_time_label(payload.get("updated_at")),
        changed_file_count=(
            changed_file_count
            if isinstance(changed_file_count, int)
            else len(latest_changed_files)
        ),
        latest_changed_files=latest_changed_files,
        default_model=_optional_str(payload.get("default_model")),
        thinking_mode=_optional_str(payload.get("thinking_mode")),
        local_memory_enabled=payload.get("local_memory_enabled") is True,
        provider_memory=_optional_str(payload.get("provider_memory")),
    )


def changed_file_summary_from_mapping(
    payload: dict[str, object] | str,
) -> ChangedFileSummary:
    if isinstance(payload, str):
        return ChangedFileSummary(path=payload, display_path=_display_path(payload))
    path = str(payload.get("path") or payload.get("display_path") or "-")
    status = str(payload.get("status") or "updated")
    display_path = payload.get("display_path")
    return ChangedFileSummary(
        path=path,
        status=status if status in {"created", "updated", "deleted"} else "updated",
        display_path=(
            str(display_path) if isinstance(display_path, str) else _display_path(path)
        ),
    )


def changed_file_summaries_from_payload(
    value: object,
) -> tuple[ChangedFileSummary, ...]:
    return tuple(
        changed_file_summary_from_mapping(item)
        for item in _mapping_or_string_list(value)
    )


def _mapping_list(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _mapping_or_string_list(value: object) -> list[dict[str, object] | str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict | str)]


def _display_path(path: str) -> str:
    normalized = path.replace("\\", "/")
    marker = "/mnt/user-data/workspace/"
    if normalized.startswith(marker):
        return normalized.removeprefix(marker)
    return path


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _relative_time_label(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        updated = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    now = datetime.now(UTC)
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=UTC)
    seconds = max(0, int((now - updated).total_seconds()))
    if seconds < 60:
        return "now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    return f"{days}d ago"
