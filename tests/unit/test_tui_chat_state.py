from pathlib import Path

from awesome_agent.cli.repo_context import CliLaunchContext
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


def test_chat_session_stores_launch_context(tmp_path: Path) -> None:
    context = CliLaunchContext(project_root=tmp_path, context_kind="workspace")

    state = ChatSessionState.new(launch_context=context)

    assert state.launch_context == context
    assert state.context_label == f"workspace: {tmp_path}"


def test_chat_state_toggles_details() -> None:
    state = ChatSessionState.new()

    updated = state.toggle_details()

    assert updated.details_enabled is True
    assert updated.toggle_details().details_enabled is False
