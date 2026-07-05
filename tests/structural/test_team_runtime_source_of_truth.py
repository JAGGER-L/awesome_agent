from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCOPED_ROUTE = "team-coding" + "-scoped"
SCOPED_ROUTE_CONSTANT = "SCOPED_TEAM" + "_CODING_ROUTE"
SCOPED_GRAPH_CLASS = "Team" + "CodingGraph"


def test_worker_app_does_not_reference_scoped_team_route() -> None:
    text = (ROOT / "src" / "awesome_agent" / "runtime" / "worker_app.py").read_text(
        encoding="utf-8"
    )

    assert SCOPED_ROUTE not in text
    assert f"{SCOPED_GRAPH_CLASS}(" not in text
    assert SCOPED_ROUTE_CONSTANT not in text
    assert "team_provider_resolver" not in text
