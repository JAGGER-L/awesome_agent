from __future__ import annotations

from awesome_agent.tui.chat_state import (
    ChatMessage,
    ChatSessionState,
    chat_messages_from_thread_records,
    should_resume_last_run,
)


def test_switch_thread_replaces_transcript_and_clears_run_state() -> None:
    state = (
        ChatSessionState.new()
        .append(ChatMessage.user("old"))
        .with_run("run-1")
        .mark_operation_paused("run-1")
    )

    switched = state.switch_thread(
        backend_thread_id="thread-2",
        title="New work",
        context_label="E:\\project",
        messages=[ChatMessage.user("restored")],
    )

    assert switched.backend_thread_id == "thread-2"
    assert switched.thread_title == "New work"
    assert switched.current_run_id is None
    assert switched.last_resumable_run_id is None
    assert [message.content for message in switched.messages] == ["restored"]


def test_should_resume_last_run_understands_continuation_phrases() -> None:
    assert should_resume_last_run("continue")
    assert should_resume_last_run(" resume ")
    assert should_resume_last_run("\u7ee7\u7eed")
    assert not should_resume_last_run("continue the plan")


def test_chat_messages_from_thread_records_maps_roles_and_kinds() -> None:
    messages = chat_messages_from_thread_records(
        [
            {"role": "user", "content": "hi", "kind": "message"},
            {"role": "assistant", "content": "hello", "kind": "model"},
            {"role": "system", "content": "done", "kind": "run"},
        ]
    )

    assert [message.role for message in messages] == ["user", "assistant", "system"]
    assert [message.content for message in messages] == ["hi", "hello", "done"]
    assert messages[2].kind == "run"
