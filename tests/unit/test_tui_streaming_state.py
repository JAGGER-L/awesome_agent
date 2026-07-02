from __future__ import annotations

from awesome_agent.tui.chat_state import ChatSessionState


def test_begin_and_finish_operation_tracks_busy_state() -> None:
    state = ChatSessionState.new()

    busy = state.begin_operation("op-1", "streaming")
    done = busy.finish_operation()

    assert busy.active_operation_id == "op-1"
    assert busy.active_operation_label == "streaming"
    assert busy.status_label == "streaming"
    assert done.active_operation_id is None
    assert done.status_label == "ready"


def test_stream_delta_preserves_partial_assistant_on_pause() -> None:
    state = ChatSessionState.new().begin_operation("op-1", "streaming")

    updated = state.append_stream_delta("hel").append_stream_delta("lo")
    paused = updated.mark_operation_paused("run-1")

    assert paused.messages[-1].role == "assistant"
    assert paused.messages[-1].content == "hello"
    assert paused.last_resumable_run_id == "run-1"
    assert paused.status_label == "paused"


def test_note_run_started_tracks_current_run() -> None:
    state = ChatSessionState.new()

    updated = state.note_run_started("run-1")

    assert updated.current_run_id == "run-1"
