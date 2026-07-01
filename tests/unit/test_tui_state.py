from awesome_agent.tui.state import approval_row, run_row


def test_run_row_marks_attention_and_terminal_statuses() -> None:
    row = run_row(
        {
            "id": "run-1",
            "goal": "Fix bug",
            "status": "recovery_required",
            "mode": "coding",
            "runtime_route": "solo-modifying",
            "dispatch_status": "terminal",
            "updated_at": "2026-07-01T00:00:00Z",
        }
    )

    assert row.attention
    assert row.terminal


def test_approval_row_projects_pending_request() -> None:
    row = approval_row(
        {
            "event_type": "approval.requested",
            "sequence": 7,
            "payload": {"approval_id": "approval-1"},
        }
    )

    assert row.pending
    assert row.id == "approval-1"


def test_tui_app_imports() -> None:
    from awesome_agent.tui.app import AwesomeAgentTui

    app = AwesomeAgentTui(api_url="http://127.0.0.1:8000")

    assert app.api_url == "http://127.0.0.1:8000"
