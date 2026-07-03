from __future__ import annotations

from collections.abc import Iterable

from rich.text import Text

from awesome_agent.safety.redaction import redact_text
from awesome_agent.surfaces.client import ChangedFileSummary
from awesome_agent.tui.chat_state import ChatEventKind, ChatMessage, ThoughtBlock
from awesome_agent.tui.events import (
    ApprovalPromptState,
    TeamDisplayEvent,
    ToolDisplayEvent,
)


def render_message(message: ChatMessage) -> Text:
    if message.kind is ChatEventKind.COMMAND:
        return Text.assemble(("> ", "bold magenta"), (message.content, "bold"))
    if message.role == "user":
        return Text.assemble(("> ", "bold cyan"), (message.content, "bold"))
    if message.role == "assistant":
        return Text.assemble(("assistant\n", "dim"), (message.content, "white"))
    if message.kind is ChatEventKind.ERROR:
        return _labeled("error", message.content, label_style="bold red")
    if message.kind is ChatEventKind.RUN:
        return _labeled("run", message.content, label_style="blue")
    if message.kind is ChatEventKind.TOOL:
        return _labeled("tool", message.content, label_style="magenta")
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
) -> Text:
    rendered = Text()
    message_list = list(messages)
    legacy_thought_inserted = False
    turn_thoughts = thought_blocks or {}
    rendered_thought_turns: set[str] = set()
    for index, message in enumerate(message_list):
        if index:
            rendered.append("\n\n")
        rendered.append_text(render_message(message))
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
    rendered = Text.assemble(("Tool - ", "magenta"), (event.name, "bold"))
    if event.summary:
        rendered.append(f"\n  {event.summary}")
    if details_enabled:
        for key, value in event.details.items():
            rendered.append(f"\n  {key}: {_bounded(str(value))}", style="dim")
    return rendered


def render_team_event(event: TeamDisplayEvent, *, details_enabled: bool) -> Text:
    rendered = Text.assemble((event.title, "blue"), ("\n  ", ""), (event.summary, ""))
    if details_enabled:
        for key, value in event.details.items():
            rendered.append(f"\n  {key}: {_bounded(str(value))}", style="dim")
    return rendered


def render_approval_prompt(prompt: ApprovalPromptState) -> Text:
    return Text(prompt.render(), style="yellow")


def render_changed_files(files: Iterable[ChangedFileSummary]) -> Text:
    file_list = list(files)
    rendered = Text("Changed files", style="green")
    if not file_list:
        rendered.append("\n  none")
        return rendered
    for item in file_list:
        rendered.append(f"\n  {item.status:<7} {item.visible_path}")
    return rendered


def _labeled(label: str, content: str, *, label_style: str) -> Text:
    return Text.assemble((f"{label}: ", label_style), (content, ""))


def _bounded(value: str, *, max_chars: int = 600) -> str:
    redacted = redact_text(value).text
    if len(redacted) <= max_chars:
        return redacted
    return f"{redacted[:300]}\n  ...\n  {redacted[-300:]}"
