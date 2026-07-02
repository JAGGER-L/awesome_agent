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


def test_thought_attaches_to_active_turn_only() -> None:
    started = datetime(2026, 1, 1, tzinfo=UTC)
    ended = started + timedelta(seconds=1)
    state = (
        ChatSessionState.new()
        .begin_turn("turn-1")
        .begin_thought(started)
        .append_thought_delta("first thought")
        .complete_thought(ended)
        .finish_turn()
        .begin_turn("turn-2")
        .begin_thought(started)
        .append_thought_delta("second thought")
    )

    first = state.thought_for_turn("turn-1")
    second = state.thought_for_turn("turn-2")

    assert first is not None
    assert second is not None
    assert first.text == "first thought"
    assert first.active is False
    assert second.text == "second thought"
    assert second.active is True


def test_toggle_thought_toggles_current_or_latest_thought() -> None:
    started = datetime(2026, 1, 1, tzinfo=UTC)
    state = (
        ChatSessionState.new()
        .begin_turn("turn-1")
        .begin_thought(started)
        .append_thought_delta("first")
        .complete_thought(started)
        .finish_turn()
    )

    expanded = state.toggle_thought()
    collapsed = expanded.toggle_thought()
    expanded_thought = expanded.thought_for_turn("turn-1")
    collapsed_thought = collapsed.thought_for_turn("turn-1")

    assert expanded_thought is not None
    assert collapsed_thought is not None
    assert expanded_thought.collapsed is False
    assert collapsed_thought.collapsed is True


def test_staged_skills_clear_after_turn() -> None:
    state = ChatSessionState.new().stage_skill("repository-inspection")

    assert state.staged_skill_ids == ("repository-inspection",)
    assert state.clear_staged_skills().staged_skill_ids == ()


def test_conversation_controls_update_state() -> None:
    state = (
        ChatSessionState.new()
        .with_model("deepseek-v4-flash")
        .with_thinking("off")
        .with_local_memory(True)
        .with_provider_memory("mem0")
    )

    assert state.current_model == "deepseek-v4-flash"
    assert state.thinking_mode == "off"
    assert state.local_memory_enabled is True
    assert state.provider_memory == "mem0"
