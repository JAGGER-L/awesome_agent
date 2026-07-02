from __future__ import annotations

from awesome_agent.tui.chat_state import ChatEventKind, ChatMessage, ThoughtBlock
from awesome_agent.tui.rendering import (
    render_message,
    render_thought,
    render_transcript,
)


def test_user_message_uses_prompt_marker() -> None:
    rendered = render_message(ChatMessage.user("What can you do?"))

    assert rendered.plain.startswith("> What can you do?")


def test_assistant_message_uses_answer_marker() -> None:
    rendered = render_message(ChatMessage.assistant("I can help.")).plain

    assert "assistant" in rendered
    assert "I can help." in rendered
    assert not rendered.startswith("[message]")


def test_error_message_is_actionable() -> None:
    rendered = render_message(ChatMessage.error("Provider timed out")).plain

    assert "error" in rendered.lower()
    assert "Provider timed out" in rendered


def test_normal_messages_do_not_expose_internal_kind_prefixes() -> None:
    messages = [
        ChatMessage.user("hi"),
        ChatMessage.assistant("hello"),
        ChatMessage.system("ready"),
        ChatMessage.system("Run started", kind=ChatEventKind.RUN),
    ]

    rendered = [render_message(message).plain for message in messages]

    assert not any(
        item.startswith(("[message]", "[model]", "[you]")) for item in rendered
    )


def test_transcript_separates_messages_with_blank_lines() -> None:
    transcript = render_transcript(
        [ChatMessage.user("hi"), ChatMessage.assistant("hello")]
    ).plain

    assert transcript == "> hi\n\nassistant\nhello"


def test_collapsed_thought_hides_reasoning_text() -> None:
    rendered = render_thought(
        ThoughtBlock(
            text="private reasoning",
            active=False,
            collapsed=True,
            elapsed_seconds=1,
        )
    ).plain

    assert rendered == "Thought for 1s (ctrl+o to expand)"
    assert "private reasoning" not in rendered


def test_expanded_thought_shows_reasoning_text() -> None:
    rendered = render_thought(
        ThoughtBlock(
            text="bounded reasoning",
            active=False,
            collapsed=False,
            elapsed_seconds=1,
        )
    ).plain

    assert "ctrl+o to collapse" in rendered
    assert "bounded reasoning" in rendered
