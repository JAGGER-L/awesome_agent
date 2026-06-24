import json
from pathlib import Path
from uuid import uuid4

import pytest
from tests.fakes import FakeModelProvider

from awesome_agent.orchestration.leader import SoloLeaderRuntime
from awesome_agent.orchestration.plans import PlanHistory


def _response(*, use_team: bool) -> str:
    return json.dumps(
        {
            "objective": "Implement a small change",
            "use_team": use_team,
            "reasoning": "One bounded workflow",
            "tasks": [
                {
                    "title": "Implement",
                    "description": "Make the change",
                    "acceptance_criteria": ["Tests pass"],
                }
            ],
        }
    )


@pytest.mark.asyncio
async def test_solo_leader_creates_plan() -> None:
    provider = FakeModelProvider([_response(use_team=False)])
    runtime = SoloLeaderRuntime(provider)

    result = await runtime.run(run_id=uuid4(), goal="Make a small change")

    assert result["plan"]["use_team"] is False
    assert result["final_report"] == "Solo plan created with 1 task(s)."
    assert provider.requests[0].user_prompt == "Make a small change"


@pytest.mark.asyncio
async def test_solo_leader_defers_team_task() -> None:
    runtime = SoloLeaderRuntime(FakeModelProvider([_response(use_team=True)]))

    result = await runtime.run(run_id=uuid4(), goal="Build frontend and backend")

    assert "requires team mode" in result["final_report"]


@pytest.mark.asyncio
async def test_solo_leader_persists_plan_snapshot(tmp_path: Path) -> None:
    run_id = uuid4()
    history = PlanHistory(run_id=run_id)
    path = tmp_path / "plan.json"
    runtime = SoloLeaderRuntime(FakeModelProvider([_response(use_team=False)]))

    await runtime.run(
        run_id=run_id,
        goal="Make a small change",
        plan_history=history,
        export_path=path,
    )

    assert history.current is not None
    assert path.exists()
