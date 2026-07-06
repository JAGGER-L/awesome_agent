from __future__ import annotations

from collections.abc import Iterable

from rich.text import Text

from awesome_agent.safety.redaction import redact_text
from awesome_agent.surfaces.client import ChangedFileSummary
from awesome_agent.tui.chat_state import (
    ChatEventKind,
    ChatMessage,
    ThoughtBlock,
    ToolGroup,
)
from awesome_agent.tui.events import (
    ApprovalPromptState,
    TeamDisplayEvent,
    TeamStatusDisplay,
    ToolDisplayEvent,
    ToolTimelineEntry,
)


def render_message(
    message: ChatMessage,
    *,
    changed_files_expanded: bool = False,
    tool_groups_expanded: bool = False,
) -> Text:
    if message.kind is ChatEventKind.COMMAND:
        return Text.assemble(("> ", "bold magenta"), (message.content, "bold"))
    if message.role == "user":
        rendered = Text.assemble(("> ", "bold cyan"), (message.content, "bold"))
        attachment_line = _attachment_summary(message.attachments)
        if attachment_line:
            rendered.append(f"\n{attachment_line}", style="dim")
        return rendered
    if message.role == "assistant":
        rendered = Text.assemble(("assistant\n", "dim"), (message.content, "white"))
        if message.changed_files:
            rendered.append("\n")
            rendered.append_text(
                render_changed_files(
                    message.changed_files,
                    expanded=changed_files_expanded,
                )
            )
        return rendered
    if message.kind is ChatEventKind.ERROR:
        return _labeled("error", message.content, label_style="bold red")
    if message.kind is ChatEventKind.RUN:
        return _labeled("run", message.content, label_style="blue")
    if message.kind is ChatEventKind.TOOL:
        if message.tool_group is not None:
            return _render_tool_group(
                message,
                expanded=tool_groups_expanded,
            )
        return _labeled(
            "tool",
            message.content,
            label_style=_tool_label_style(message.content),
        )
    if message.kind is ChatEventKind.ARTIFACT:
        return _labeled("artifact", message.content, label_style="green")
    if message.kind is ChatEventKind.APPROVAL:
        return _labeled("approval", message.content, label_style="yellow")
    return _labeled("note", message.content, label_style="dim")


def render_transcript(
    messages: Iterable[ChatMessage],
    *,
    thought: ThoughtBlock | None = None,
    thought_blocks: dict[str, ThoughtBlock] | None = None,
    changed_files_expanded: bool = False,
    tool_groups_expanded: bool = False,
) -> Text:
    rendered = Text()
    message_list = list(messages)
    legacy_thought_inserted = False
    turn_thoughts = thought_blocks or {}
    rendered_thought_turns: set[str] = set()
    for index, message in enumerate(message_list):
        if index:
            rendered.append("\n\n")
        rendered.append_text(
            render_message(
                message,
                changed_files_expanded=changed_files_expanded,
                tool_groups_expanded=tool_groups_expanded,
            )
        )
        if (
            message.role == "user"
            and message.turn_id is not None
            and message.turn_id in turn_thoughts
            and message.turn_id not in rendered_thought_turns
        ):
            rendered.append("\n\n")
            rendered.append_text(render_thought(turn_thoughts[message.turn_id]))
            rendered_thought_turns.add(message.turn_id)
        elif (
            thought is not None
            and not turn_thoughts
            and not legacy_thought_inserted
            and message.role == "user"
        ):
            rendered.append("\n\n")
            rendered.append_text(render_thought(thought))
            legacy_thought_inserted = True
    if thought is not None and not turn_thoughts and not legacy_thought_inserted:
        if message_list:
            rendered.append("\n\n")
        rendered.append_text(render_thought(thought))
    return rendered


def render_thought(thought: ThoughtBlock) -> Text:
    if thought.active:
        label = "Thinking ..."
    else:
        seconds = thought.elapsed_seconds if thought.elapsed_seconds is not None else 0
        label = f"Thought for {seconds}s (ctrl+o to expand)"
        if not thought.collapsed:
            label = f"Thought for {seconds}s (ctrl+o to collapse)"
    rendered = Text(label, style="dim")
    if thought.collapsed:
        return rendered
    rendered.append("\n")
    rendered.append(thought.text, style="dim")
    if thought.truncated:
        rendered.append("\n[truncated]", style="yellow")
    return rendered


def render_tool_event(event: ToolDisplayEvent, *, details_enabled: bool) -> Text:
    label_style = _tool_label_style(event.summary)
    rendered = Text.assemble(("Tool - ", label_style), (event.name, "bold"))
    if event.summary:
        rendered.append(f"\n  {event.summary}")
    if details_enabled:
        for key, value in event.details.items():
            rendered.append(f"\n  {key}: {_bounded(str(value))}", style="dim")
    return rendered


def _render_tool_group(message: ChatMessage, *, expanded: bool) -> Text:
    assert message.tool_group is not None
    action = "collapse" if expanded else "expand"
    summary = message.content or _tool_group_summary(message.tool_group)
    rendered = _labeled(
        "tools",
        f"{summary} (ctrl+t to {action})",
        label_style="cyan",
    )
    if not expanded:
        return rendered
    for entry in message.tool_group.entries:
        style = "red" if entry.failed else "green" if entry.completed else "dim"
        rendered.append(f"\n  {_tool_entry_header(entry)}", style=style)
        for line in _tool_change_lines(entry):
            rendered.append(f"\n    {line}", style="dim")
        for key, value in entry.details.items():
            rendered.append(f"\n    {key}: {_bounded(str(value))}", style="dim")
    return rendered


def _tool_group_summary(group: ToolGroup) -> str:
    total = group.total
    running = group.running
    completed = group.completed
    failed = group.failed
    pieces = [f"called {total}"]
    if running:
        pieces.append(f"{running} running")
    if completed:
        pieces.append(f"{completed} completed")
    if failed:
        pieces.append(f"{failed} failed")
    additions, deletions, files = _aggregate_change_stats(group)
    if files:
        suffix = "file" if files == 1 else "files"
        pieces.append(f"+{additions} -{deletions}")
        pieces.append(f"{files} {suffix}")
    return ", ".join(pieces)


def _tool_entry_header(entry: ToolTimelineEntry) -> str:
    status = entry.status or entry.summary
    header = f"{entry.name} - {status}"
    duration = _duration_label(entry.duration_ms)
    changes = _change_stats_label(entry.change_stats)
    if duration:
        header = f"{header} in {duration}"
    if changes:
        header = f"{header}, {changes}"
    return header


def _duration_label(duration_ms: int | None) -> str:
    if duration_ms is None:
        return ""
    if duration_ms < 1000:
        return f"{duration_ms}ms"
    seconds = duration_ms / 1000
    return f"{seconds:.1f}s"


def _change_stats_label(stats: dict[str, object] | None) -> str:
    if not isinstance(stats, dict):
        return ""
    additions = _int_value(stats.get("additions"))
    deletions = _int_value(stats.get("deletions"))
    if additions == 0 and deletions == 0:
        return ""
    return f"+{additions} -{deletions}"


def _tool_change_lines(entry: ToolTimelineEntry) -> list[str]:
    stats = entry.change_stats
    if not isinstance(stats, dict):
        return []
    raw_items = stats.get("items")
    if not isinstance(raw_items, list):
        return []
    lines: list[str] = []
    for raw in raw_items[:8]:
        if not isinstance(raw, dict):
            continue
        path = raw.get("path")
        if not isinstance(path, str) or not path:
            continue
        status = str(raw.get("status") or "changed")
        additions = _int_value(raw.get("additions"))
        deletions = _int_value(raw.get("deletions"))
        change = f" +{additions} -{deletions}" if additions or deletions else ""
        lines.append(f"{status} {_single_line_path(path)}{change}")
    remaining = len(raw_items) - len(lines)
    if remaining > 0:
        suffix = "file" if remaining == 1 else "files"
        lines.append(f"{remaining} more {suffix}")
    return lines


def _aggregate_change_stats(group: ToolGroup) -> tuple[int, int, int]:
    additions = 0
    deletions = 0
    files = 0
    for entry in group.entries:
        stats = entry.change_stats
        if not isinstance(stats, dict):
            continue
        additions += _int_value(stats.get("additions"))
        deletions += _int_value(stats.get("deletions"))
        files += _int_value(stats.get("files"))
    return additions, deletions, files


def _int_value(value: object) -> int:
    return value if isinstance(value, int) and value > 0 else 0


def _tool_label_style(summary: str) -> str:
    normalized = summary.strip().lower()
    if any(value in normalized for value in ("failed", "error", "cancelled")):
        return "red"
    if any(
        value in normalized for value in ("completed", "success", "succeeded", "done")
    ):
        return "green"
    return "magenta"


def render_team_event(event: TeamDisplayEvent, *, details_enabled: bool) -> Text:
    rendered = Text.assemble((event.title, "blue"), ("\n  ", ""), (event.summary, ""))
    if details_enabled:
        for key, value in event.details.items():
            rendered.append(f"\n  {key}: {_bounded(str(value))}", style="dim")
    return rendered


def render_team_status(event: TeamStatusDisplay) -> Text:
    rendered = Text.assemble(
        ("Team", "blue"),
        (f" {event.phase}", "dim"),
    )
    for line in event.lines:
        rendered.append(f"\n  {line}")
    return rendered


def render_approval_prompt(prompt: ApprovalPromptState) -> Text:
    return Text(prompt.render(), style="yellow")


def render_changed_files(
    files: Iterable[ChangedFileSummary],
    *,
    expanded: bool = False,
    visible_limit: int = 3,
) -> Text:
    file_list = list(files)
    rendered = Text("Changed files", style="green")
    if not file_list:
        rendered.append("\n  none")
        return rendered
    visible = file_list if expanded else file_list[:visible_limit]
    for item in visible:
        rendered.append(
            f"\n  {_changed_file_status(item.status):<7} "
            f"{_single_line_path(item.visible_path)}"
        )
    remaining = len(file_list) - len(visible)
    if remaining > 0:
        suffix = "file" if remaining == 1 else "files"
        rendered.append(
            f"\n  {remaining} more {suffix} (ctrl+e to expand)",
            style="dim",
        )
    elif len(file_list) > visible_limit:
        rendered.append("\n  ctrl+e to collapse", style="dim")
    return rendered


def _changed_file_status(value: str) -> str:
    if value in {"created", "updated", "deleted", "unknown"}:
        return value
    return "unknown"


def _single_line_path(value: str, *, max_chars: int = 96) -> str:
    one_line = value.replace("\r", " ").replace("\n", " ")
    if len(one_line) <= max_chars:
        return one_line
    return f"...{one_line[-(max_chars - 3) :]}"


def render_pending_attachments(attachments: Iterable[dict[str, object]]) -> Text:
    attachment_list = list(attachments)
    if not attachment_list:
        return Text()
    rendered = Text("Pending attachments", style="cyan")
    for item in attachment_list:
        filename = str(item.get("filename") or "attachment")
        size = item.get("size")
        size_label = f" ({size} bytes)" if isinstance(size, int) else ""
        rendered.append(f"\n  {filename}{size_label}")
    return rendered


def _labeled(label: str, content: str, *, label_style: str) -> Text:
    return Text.assemble((f"{label}: ", label_style), (content, ""))


def _bounded(value: str, *, max_chars: int = 600) -> str:
    redacted = redact_text(value).text
    if len(redacted) <= max_chars:
        return redacted
    return f"{redacted[:300]}\n  ...\n  {redacted[-300:]}"


def _attachment_summary(attachments: Iterable[dict[str, object]]) -> str:
    names = [
        str(item.get("filename") or "attachment")
        for item in attachments
        if isinstance(item, dict)
    ]
    if not names:
        return ""
    return f"Attachments: {', '.join(names)}"
