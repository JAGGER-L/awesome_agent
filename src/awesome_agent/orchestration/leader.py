from __future__ import annotations

from pathlib import Path
from typing import Any, TypedDict, cast
from uuid import UUID

from langgraph.graph import END, START, StateGraph

from awesome_agent.modeling import (
    ModelProvider,
    ModelRequest,
    SystemMessage,
    UserMessage,
)
from awesome_agent.orchestration.plans import LeaderPlan, PlanHistory

_PLAN_SYSTEM_PROMPT = """You are the Leader of a coding-agent runtime.
Return only JSON matching this schema:
{
  "objective": "string",
  "use_team": false,
  "reasoning": "string",
  "tasks": [
    {
      "title": "string",
      "description": "string",
      "acceptance_criteria": ["string"]
    }
  ]
}
Use team mode only when the task has independent workstreams, distinct
specialties, meaningful parallelism, durable responsibilities, or excessive
single-context complexity.
"""


class LeaderState(TypedDict, total=False):
    run_id: str
    goal: str
    plan: dict[str, Any]
    final_report: str


class SoloLeaderRuntime:
    def __init__(self, provider: ModelProvider) -> None:
        self._provider = provider

    async def _create_plan(self, state: LeaderState) -> LeaderState:
        turn = await self._provider.complete(
            ModelRequest(
                messages=[
                    SystemMessage(content=_PLAN_SYSTEM_PROMPT),
                    UserMessage(content=state["goal"]),
                ],
            )
        )
        plan = LeaderPlan.model_validate_json(turn.assistant.content)
        return {"plan": plan.model_dump(mode="json")}

    async def _complete(self, state: LeaderState) -> LeaderState:
        plan = LeaderPlan.model_validate(state["plan"])
        if plan.use_team:
            report = "Task requires team mode; execution is deferred to team runtime."
        else:
            report = f"Solo plan created with {len(plan.tasks)} task(s)."
        return {"final_report": report}

    def build(self) -> Any:
        graph = StateGraph(LeaderState)
        graph.add_node("create_plan", self._create_plan)
        graph.add_node("complete", self._complete)
        graph.add_edge(START, "create_plan")
        graph.add_edge("create_plan", "complete")
        graph.add_edge("complete", END)
        return graph.compile(name="solo-leader")

    async def run(
        self,
        *,
        run_id: UUID,
        goal: str,
        plan_history: PlanHistory | None = None,
        export_path: Path | None = None,
    ) -> LeaderState:
        graph = self.build()
        result = await graph.ainvoke({"run_id": str(run_id), "goal": goal})
        state = cast(LeaderState, result)
        if plan_history is not None:
            plan_history.revise(
                LeaderPlan.model_validate(state["plan"]),
                reason="Leader created the initial plan.",
            )
            if export_path is not None:
                plan_history.export(export_path)
        return state
