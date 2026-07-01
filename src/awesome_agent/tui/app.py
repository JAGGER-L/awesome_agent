from __future__ import annotations

from collections.abc import Sequence
from typing import Any, ClassVar, cast

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Footer, Header, Static

from awesome_agent.tui.client import TuiApiClient
from awesome_agent.tui.state import approval_row, run_row
from awesome_agent.tui.widgets import configure_run_table, replace_run_rows


class AwesomeAgentTui(App[None]):
    TITLE = "awesome_agent"
    SUB_TITLE = "Run operator console"
    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        ("r", "refresh", "Refresh"),
        ("c", "cancel_run", "Cancel"),
        ("u", "resume_run", "Resume"),
        ("a", "approve", "Approve"),
        ("d", "deny", "Deny"),
        ("q", "quit", "Quit"),
    ]

    def __init__(
        self,
        *,
        api_url: str,
        run_id: str | None = None,
        refresh_interval: float = 2.0,
        client: TuiApiClient | None = None,
    ) -> None:
        super().__init__()
        self.api_url = api_url
        self.initial_run_id = run_id
        self.refresh_interval = refresh_interval
        self.client = client or TuiApiClient(api_url)
        self.selected_run_id: str | None = run_id
        self.pending_approval_id: str | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            yield DataTable[str](id="runs")
            with Vertical(id="detail"):
                yield Static("No run selected", id="summary")
                yield Static("", id="diagnostics")
                yield Static("", id="approvals")
                yield Static("", id="events")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#runs", DataTable)
        configure_run_table(cast(DataTable[str], table))
        self.set_interval(self.refresh_interval, self.reload_data)
        self.reload_data()

    def reload_data(self) -> None:
        runs = [run_row(payload) for payload in self.client.list_runs()]
        table = cast(DataTable[str], self.query_one("#runs", DataTable))
        replace_run_rows(table, runs)
        if self.selected_run_id is None and runs:
            self.selected_run_id = runs[0].id
        if self.selected_run_id is not None:
            self._refresh_detail(self.selected_run_id)

    def _refresh_detail(self, run_id: str) -> None:
        run = self.client.get_run(run_id)
        diagnostics = self.client.diagnostics(run_id)
        approvals = [approval_row(event) for event in self.client.approvals(run_id)]
        events = self.client.events(run_id)[-8:]
        pending = [approval for approval in approvals if approval.pending]
        self.pending_approval_id = pending[-1].id if pending else None

        self.query_one("#summary", Static).update(
            f"{run['id']}\n"
            f"status={run['status']} dispatch={run.get('dispatch_status')}\n"
            f"route={run.get('runtime_route')} mode={run.get('mode')}\n"
            f"{run.get('goal')}"
        )
        self.query_one("#diagnostics", Static).update(_diagnostics_text(diagnostics))
        self.query_one("#approvals", Static).update(_approvals_text(approvals))
        self.query_one("#events", Static).update(_events_text(events))

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.selected_run_id = str(event.row_key.value)
        self._refresh_detail(self.selected_run_id)

    def action_refresh(self) -> None:
        self.reload_data()

    def action_cancel_run(self) -> None:
        if self.selected_run_id is not None:
            self.client.cancel(self.selected_run_id)
            self.reload_data()

    def action_resume_run(self) -> None:
        if self.selected_run_id is not None:
            self.client.resume(self.selected_run_id)
            self.reload_data()

    def action_approve(self) -> None:
        self._decide_pending(approved=True)

    def action_deny(self) -> None:
        self._decide_pending(approved=False)

    def _decide_pending(self, *, approved: bool) -> None:
        if self.selected_run_id is None or self.pending_approval_id is None:
            return
        self.client.decide_approval(
            self.selected_run_id,
            self.pending_approval_id,
            approved=approved,
        )
        self.reload_data()


def _diagnostics_text(payload: dict[str, Any]) -> str:
    status = payload.get("status", {})
    dispatch = payload.get("dispatch", {})
    budgets = payload.get("budgets", {})
    if not isinstance(status, dict):
        status = {}
    if not isinstance(dispatch, dict):
        dispatch = {}
    if not isinstance(budgets, dict):
        budgets = {}
    return (
        "Diagnostics\n"
        f"status={status.get('status')} dispatch={dispatch.get('status')}\n"
        f"budget={budgets.get('threshold_status')}"
    )


def _approvals_text(approvals: Sequence[object]) -> str:
    pending = [row for row in approvals if getattr(row, "pending", False)]
    return f"Approvals\npending={len(pending)} total={len(approvals)}"


def _events_text(events: list[dict[str, Any]]) -> str:
    lines = ["Recent Events"]
    for event in events:
        lines.append(f"{event.get('sequence')} {event.get('event_type')}")
    return "\n".join(lines)
