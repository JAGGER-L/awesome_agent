from __future__ import annotations

from datetime import UTC, datetime, timedelta

from awesome_agent.tui.chat_state import ChatSessionState


def test_thought_state_completes_collapsed_with_elapsed_time() -> None:
    started = datetime(2026, 1, 1, tzinfo=UTC)
    ended = started + timedelta(seconds=2)

    state = (
        ChatSessionState.new()
        .begin_thought(started)
        .append_thought_delta("Inspecting.")
        .complete_thought(ended)
    )

    thought = state.thought_block()
    assert thought is not None
    assert thought.active is False
    assert thought.collapsed is True
    assert thought.elapsed_seconds == 2
    assert thought.text == "Inspecting."


def test_thought_toggle_expands_and_collapses() -> None:
    started = datetime(2026, 1, 1, tzinfo=UTC)
    state = (
        ChatSessionState.new()
        .begin_thought(started)
        .append_thought_delta("hidden")
        .complete_thought(started)
    )

    expanded = state.toggle_thought()
    collapsed = expanded.toggle_thought()

    assert expanded.thought_collapsed is False
    assert collapsed.thought_collapsed is True


def test_thought_delta_is_bounded_and_marked_truncated() -> None:
    state = ChatSessionState.new().begin_thought(datetime(2026, 1, 1, tzinfo=UTC))

    updated = state.append_thought_delta("abcdef", max_chars=3)

    assert updated.thought_text == "abc"
    assert updated.thought_truncated is True
