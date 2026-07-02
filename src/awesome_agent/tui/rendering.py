from __future__ import annotations

from awesome_agent.tui.chat_state import ChatMessage


def render_message(message: ChatMessage) -> str:
    prefix = "you" if message.role == "user" else message.kind.value
    return f"[{prefix}] {message.content}"
