from pathlib import Path

from awesome_agent.cli.repo_context import CliLaunchContext
from awesome_agent.surfaces.client import ChangedFileSummary
from awesome_agent.tui.chat_state import (
    ChatEventKind,
    ChatMessage,
    ChatSessionState,
    chat_messages_from_thread_records,
)
from awesome_agent.tui.status_panel import StatusPanelTab


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


def test_chat_state_opens_and_closes_status_panel() -> None:
    state = ChatSessionState.new()

    opened = state.open_status_panel()

    assert opened.active_status_tab is StatusPanelTab.STATUS
    assert opened.close_status_panel().active_status_tab is None


def test_chat_state_cycles_status_tabs() -> None:
    state = ChatSessionState.new().open_status_panel()

    assert state.next_status_tab().active_status_tab is StatusPanelTab.CONFIG
    assert state.next_status_tab().next_status_tab().active_status_tab is (
        StatusPanelTab.USAGE
    )
    assert state.previous_status_tab().active_status_tab is StatusPanelTab.USAGE


def test_switch_thread_closes_status_panel() -> None:
    state = ChatSessionState.new().open_status_panel()

    switched = state.switch_thread(
        backend_thread_id="thread-1",
        title="Restored",
        context_label="E:/project",
        messages=[],
    )

    assert switched.active_status_tab is None


def test_assistant_message_can_carry_changed_files() -> None:
    message = ChatMessage.assistant(
        "Done.",
        changed_files=(ChangedFileSummary(path="snake.html", status="created"),),
    )

    assert message.changed_files[0].path == "snake.html"
    assert message.changed_files[0].status == "created"


def test_chat_state_attaches_changed_files_to_latest_assistant() -> None:
    state = ChatSessionState.new().upsert_streaming_assistant("Done.")

    updated = state.with_latest_assistant_changed_files(
        [
            {"path": "/mnt/user-data/workspace/snake.html", "status": "created"},
            {"path": "README.md", "status": "updated"},
        ]
    )

    assert len(updated.messages) == 1
    assert updated.messages[0].role == "assistant"
    assert [item.visible_path for item in updated.messages[0].changed_files] == [
        "snake.html",
        "README.md",
    ]


def test_thread_record_restore_reads_assistant_changed_files_metadata() -> None:
    messages = chat_messages_from_thread_records(
        [
            {
                "role": "assistant",
                "content": "Done.",
                "metadata": {
                    "changed_files": [
                        {
                            "path": "/mnt/user-data/workspace/snake.html",
                            "status": "created",
                        }
                    ]
                },
            }
        ]
    )

    assert messages[0].changed_files[0].visible_path == "snake.html"
