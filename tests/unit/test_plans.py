import json
from pathlib import Path
from uuid import uuid4

from awesome_agent.orchestration.plans import LeaderPlan, PlanHistory, PlannedTask


def _plan(objective: str) -> LeaderPlan:
    return LeaderPlan(
        objective=objective,
        reasoning="bounded task",
        tasks=[PlannedTask(title="Implement", acceptance_criteria=["tests pass"])],
    )


def test_plan_history_tracks_revisions() -> None:
    history = PlanHistory(run_id=uuid4())

    first = history.revise(_plan("first"), reason="initial")
    second = history.revise(_plan("second"), reason="scope changed")

    assert first.number == 1
    assert second.number == 2
    assert history.current == second


def test_plan_history_exports_json(tmp_path: Path) -> None:
    path = tmp_path / "plan.json"
    history = PlanHistory(run_id=uuid4())
    history.revise(_plan("export"), reason="initial")

    history.export(path)

    exported = json.loads(path.read_text(encoding="utf-8"))
    assert exported["revisions"][0]["plan"]["objective"] == "export"
