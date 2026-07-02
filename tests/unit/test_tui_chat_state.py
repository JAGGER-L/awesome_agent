from awesome_agent.tui.chat_state import (
    ChatEventKind,
    ChatMessage,
    ChatSessionState,
)


def test_chat_session_starts_empty() -> None:
    state = ChatSessionState.new()

    assert state.thread_id is not None
    assert state.current_run_id is None
    assert state.messages == []
    assert state.status_label == "ready"


def test_chat_state_appends_user_and_system_messages() -> None:
    state = ChatSessionState.new()

    updated = state.append(ChatMessage.user("build a snake game")).append(
        ChatMessage.system("Run created", kind=ChatEventKind.RUN)
    )

    assert [message.role for message in updated.messages] == ["user", "system"]
    assert updated.messages[1].kind is ChatEventKind.RUN
