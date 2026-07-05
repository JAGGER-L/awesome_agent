from __future__ import annotations

from uuid import uuid4

import pytest

from awesome_agent.domain.enums import ExecutionKind, RunIntent
from awesome_agent.domain.models import Run
from awesome_agent.runtime.dispatch import IncompatibleGraphError
from awesome_agent.runtime.graph_router import GraphRouteMap
from awesome_agent.runtime.graphs import (
    CONVERSATION_TURN_ROUTE,
    MODIFYING_CODING_ROUTE,
    READ_ONLY_CODING_ROUTE,
    TEAM_CODING_ROUTE,
    TEAM_ROLE_ROUTE,
    TEAM_VERIFIER_ROUTE,
)


def _run(*, kind: ExecutionKind, route: str) -> Run:
    return Run(
        id=uuid4(),
        goal="fixture",
        intent=(
            RunIntent.CONVERSATION
            if kind is ExecutionKind.CONVERSATION
            else RunIntent.READ_ONLY
        ),
        execution_kind=kind,
        runtime_route=route,
    )


def test_graph_route_map_accepts_configured_conversation_route() -> None:
    routes = GraphRouteMap(conversation_graph=object())

    selected = routes.select(
        _run(kind=ExecutionKind.CONVERSATION, route=CONVERSATION_TURN_ROUTE)
    )

    assert selected.name == "conversation"
    assert selected.graph is not None


def test_graph_route_map_rejects_wrong_conversation_route() -> None:
    routes = GraphRouteMap(conversation_graph=object())

    with pytest.raises(IncompatibleGraphError):
        routes.select(
            _run(kind=ExecutionKind.CONVERSATION, route=READ_ONLY_CODING_ROUTE)
        )


@pytest.mark.parametrize(
    ("route", "field"),
    [
        (READ_ONLY_CODING_ROUTE, "coding_graph"),
        (MODIFYING_CODING_ROUTE, "modifying_graph"),
        (TEAM_CODING_ROUTE, "team_leader_graph"),
        (TEAM_ROLE_ROUTE, "team_role_graph"),
        (TEAM_VERIFIER_ROUTE, "team_verifier_graph"),
    ],
)
def test_graph_route_map_selects_coding_routes(route: str, field: str) -> None:
    routes = GraphRouteMap(**{field: object()})

    selected = routes.select(_run(kind=ExecutionKind.CODING, route=route))

    assert selected.runtime_route == route
    assert selected.graph is not None


def test_graph_route_map_rejects_unconfigured_route() -> None:
    routes = GraphRouteMap()

    with pytest.raises(IncompatibleGraphError):
        routes.select(_run(kind=ExecutionKind.CODING, route=MODIFYING_CODING_ROUTE))


def test_retired_scoped_team_route_is_not_selectable() -> None:
    routes = GraphRouteMap(
        probe_graph=object(),
        team_leader_graph=object(),
        team_role_graph=object(),
        team_verifier_graph=object(),
    )

    with pytest.raises(IncompatibleGraphError):
        routes.select(
            _run(kind=ExecutionKind.CODING, route="team-coding" + "-scoped")
        )
