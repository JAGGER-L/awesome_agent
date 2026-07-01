from __future__ import annotations

from textual.widgets import DataTable

from awesome_agent.tui.state import RunRow


def configure_run_table(table: DataTable[str]) -> None:
    table.cursor_type = "row"
    table.add_columns("status", "dispatch", "mode", "route", "updated", "goal", "id")


def replace_run_rows(table: DataTable[str], rows: list[RunRow]) -> None:
    table.clear()
    for row in rows:
        status = f"! {row.status}" if row.attention else row.status
        table.add_row(
            status,
            row.dispatch_status,
            row.mode,
            row.runtime_route,
            row.updated_at or "",
            row.goal,
            row.id,
            key=row.id,
        )
