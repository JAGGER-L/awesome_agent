from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class ToolDisplayEvent:
    name: str
    summary: str
    details: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TeamDisplayEvent:
    title: str
    summary: str
    details: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ApprovalPromptState:
    run_id: str
    approval_id: str
    title: str
    subject: str
    approval_type: str = "edit"
    active_index: int = 0

    @property
    def choices(self) -> tuple[str, str, str]:
        if self.approval_type == "command":
            return (
                "Yes",
                "Yes, allow similar commands during this session",
                "No",
            )
        return (
            "Yes",
            "Yes, allow all file edits during this session",
            "No",
        )

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
