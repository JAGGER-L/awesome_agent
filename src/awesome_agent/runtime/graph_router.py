from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from awesome_agent.domain.enums import ExecutionKind
from awesome_agent.domain.models import Run
from awesome_agent.runtime.dispatch import IncompatibleGraphError
from awesome_agent.runtime.graphs import (
    CONVERSATION_TURN_ROUTE,
    MODIFYING_CODING_ROUTE,
    READ_ONLY_CODING_ROUTE,
    RUNTIME_PROBE_ROUTE,
    SCOPED_TEAM_CODING_ROUTE,
    TEAM_CODING_ROUTE,
    TEAM_ROLE_ROUTE,
    TEAM_VERIFIER_ROUTE,
)


@dataclass(frozen=True, slots=True)
class SelectedGraphRoute:
    name: str
    runtime_route: str
    execution_kind: ExecutionKind
    graph: Any


@dataclass(frozen=True, slots=True)
class GraphRouteMap:
    probe_graph: Any | None = None
    conversation_graph: Any | None = None
    coding_graph: Any | None = None
    modifying_graph: Any | None = None
    team_graph: Any | None = None
    team_leader_graph: Any | None = None
    team_role_graph: Any | None = None
    team_verifier_graph: Any | None = None

    def select(self, run: Run) -> SelectedGraphRoute:
        if run.execution_kind is ExecutionKind.RUNTIME_PROBE:
            return self._configured("probe", RUNTIME_PROBE_ROUTE, run, self.probe_graph)
        if (
            run.execution_kind is ExecutionKind.CONVERSATION
            and run.runtime_route == CONVERSATION_TURN_ROUTE
        ):
            return self._configured(
                "conversation",
                CONVERSATION_TURN_ROUTE,
                run,
                self.conversation_graph,
            )
        if run.execution_kind is ExecutionKind.CODING:
            route = run.runtime_route
            if route == READ_ONLY_CODING_ROUTE:
                return self._configured("readonly", route, run, self.coding_graph)
            if route == MODIFYING_CODING_ROUTE:
                return self._configured("modifying", route, run, self.modifying_graph)
            if route == SCOPED_TEAM_CODING_ROUTE:
                return self._configured("team-scoped", route, run, self.team_graph)
            if route == TEAM_CODING_ROUTE:
                return self._configured(
                    "team-leader",
                    route,
                    run,
                    self.team_leader_graph,
                )
            if route == TEAM_ROLE_ROUTE:
                return self._configured("team-role", route, run, self.team_role_graph)
            if route == TEAM_VERIFIER_ROUTE:
                return self._configured(
                    "team-verifier",
                    route,
                    run,
                    self.team_verifier_graph,
                )
        raise IncompatibleGraphError(
            f"Worker cannot execute kind {run.execution_kind.value} route "
            f"{run.runtime_route}."
        )

    def _configured(
        self,
        name: str,
        route: str,
        run: Run,
        graph: Any | None,
    ) -> SelectedGraphRoute:
        if graph is None:
            raise IncompatibleGraphError(
                f"Worker has no configured graph for route {route}."
            )
        return SelectedGraphRoute(
            name=name,
            runtime_route=route,
            execution_kind=run.execution_kind,
            graph=graph,
        )
