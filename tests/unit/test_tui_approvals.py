from __future__ import annotations

from awesome_agent.tui.chat_state import ChatSessionState
from awesome_agent.tui.events import ApprovalPromptState


def test_approval_prompt_cycles_choices() -> None:
    prompt = ApprovalPromptState(
        run_id="run-1",
        approval_id="approval-1",
        title="Leader wants to create:",
        subject="snake-game.html",
    )

    assert prompt.move(1).active_index == 1
    assert prompt.move(-1).active_index == 2


def test_session_allow_rules_are_in_memory_state_only() -> None:
    state = ChatSessionState.new().add_session_allow_rule("edit")

    assert state.session_allow_rules == ("edit",)
    assert state.add_session_allow_rule("edit").session_allow_rules == ("edit",)
    assert ChatSessionState.new().session_allow_rules == ()


def test_switch_thread_clears_pending_approval() -> None:
    prompt = ApprovalPromptState(
        run_id="run-1",
        approval_id="approval-1",
        title="Leader wants to run:",
        subject="npm install",
        approval_type="command",
    )
    state = ChatSessionState.new().with_approval_prompt(prompt)

    switched = state.switch_thread(
        backend_thread_id="thread-2",
        title="Thread 2",
        context_label=None,
    )

    assert switched.pending_approval is None
