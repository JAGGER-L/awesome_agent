from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class ToolDisplayEvent:
    name: str
    summary: str
    details: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ToolTimelineEntry:
    name: str
    summary: str
    details: dict[str, object] = field(default_factory=dict)
    status: str = ""
    invocation_id: str | None = None
    call_id: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    duration_ms: int | None = None
    change_stats: dict[str, object] | None = None
    changed_files: tuple[dict[str, object], ...] = ()

    @property
    def running(self) -> bool:
        normalized = (self.status or self.summary).casefold()
        return normalized in {"started", "running", "progress", "approval_pending"} or (
            "running" in normalized
        )

    @property
    def failed(self) -> bool:
        normalized = (self.status or self.summary).casefold()
        return any(value in normalized for value in ("failed", "error", "cancelled"))

    @property
    def completed(self) -> bool:
        normalized = (self.status or self.summary).casefold()
        return any(
            value in normalized
            for value in ("completed", "success", "succeeded", "done")
        )


@dataclass(frozen=True, slots=True)
class TeamDisplayEvent:
    title: str
    summary: str
    details: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TeamStatusDisplay:
    root_role: str
    phase: str
    lines: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ApprovalPromptState:
    run_id: str
    approval_id: str
    title: str
    subject: str
    approval_type: str = "edit"
    active_index: int = 0

    @property
    def options(self) -> tuple[str, str, str]:
        return ("approve once", "deny", "cancel run")

    @property
    def choices(self) -> tuple[str, str, str]:
        return self.options

    def move(self, delta: int) -> ApprovalPromptState:
        return ApprovalPromptState(
            run_id=self.run_id,
            approval_id=self.approval_id,
            title=self.title,
            subject=self.subject,
            approval_type=self.approval_type,
            active_index=(self.active_index + delta) % len(self.choices),
        )

    def subject_preview(self, *, limit: int = 240) -> str:
        return _bounded_single_line(self.subject, limit=limit)

    def subject_detail(self, *, limit: int = 4000) -> str:
        return _bounded_multiline(self.subject, limit=limit)

    def render(self) -> str:
        lines = [
            f"approval: {self.title}",
            "choices:",
        ]
        for index, choice in enumerate(self.choices):
            marker = ">" if index == self.active_index else " "
            lines.append(f"{marker} {index + 1}. {choice.capitalize()}")
        lines.extend(["request:", self.subject_detail()])
        return "\n".join(lines)


def _bounded_single_line(value: str, *, limit: int) -> str:
    single = " ".join(value.split())
    if len(single) <= limit:
        return single
    return f"{single[: max(0, limit - 24)]} ... [truncated]"


def _bounded_multiline(value: str, *, limit: int) -> str:
    if len(value) <= limit:
        return value
    return f"{value[: max(0, limit - 24)]}\n...[truncated]"
