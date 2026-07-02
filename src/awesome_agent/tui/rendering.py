from __future__ import annotations

from collections.abc import Iterable

from rich.text import Text

from awesome_agent.tui.chat_state import ChatEventKind, ChatMessage


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


def render_transcript(messages: Iterable[ChatMessage]) -> Text:
    rendered = Text()
    for index, message in enumerate(messages):
        if index:
            rendered.append("\n\n")
        rendered.append_text(render_message(message))
    return rendered


def _labeled(label: str, content: str, *, label_style: str) -> Text:
    return Text.assemble((f"{label}: ", label_style), (content, ""))
