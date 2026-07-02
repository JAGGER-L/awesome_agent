from __future__ import annotations

from collections.abc import Iterable

from rich.text import Text

from awesome_agent.tui.chat_state import ChatEventKind, ChatMessage, ThoughtBlock


def render_message(message: ChatMessage) -> Text:
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
) -> Text:
    rendered = Text()
    message_list = list(messages)
    thought_inserted = False
    for index, message in enumerate(message_list):
        if index:
            rendered.append("\n\n")
        rendered.append_text(render_message(message))
        if thought is not None and not thought_inserted and message.role == "user":
            rendered.append("\n\n")
            rendered.append_text(render_thought(thought))
            thought_inserted = True
    if thought is not None and not thought_inserted:
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


def _labeled(label: str, content: str, *, label_style: str) -> Text:
    return Text.assemble((f"{label}: ", label_style), (content, ""))
