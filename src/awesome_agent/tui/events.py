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

    @property
    def failed(self) -> bool:
        normalized = self.summary.casefold()
        return any(value in normalized for value in ("failed", "error", "cancelled"))

    @property
    def completed(self) -> bool:
        normalized = self.summary.casefold()
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

    def render(self) -> str:
        lines = [self.title, f"  {self.subject}", "", "Do you want to allow this?"]
        for index, choice in enumerate(self.choices):
            marker = ">" if index == self.active_index else " "
            lines.append(f"{marker} {index + 1}. {choice}")
        return "\n".join(lines)
