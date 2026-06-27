from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import NotRequired, TypedDict, cast
from uuid import UUID

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph

from awesome_agent.agents.profiles import (
    AgentProfile,
    ProfileRegistry,
    RoleModelResolver,
)
from awesome_agent.domain.enums import AgentKind, EventType, RunMode
from awesome_agent.domain.models import Agent, Run
from awesome_agent.orchestration.team import TeamRuntime
from awesome_agent.runtime.dispatch import (
    CorruptRuntimeStateError,
    IncompatibleGraphError,
)
from awesome_agent.runtime.graphs import TEAM_CODING_GRAPH, TEAM_CODING_VERSION
from awesome_agent.runtime.repository import RuntimeRepository

__all__ = [
    "TEAM_CODING_GRAPH",
    "TEAM_CODING_VERSION",
    "TeamCodingGraph",
    "TeamCodingState",
]


class TeamCodingState(TypedDict):
    run_id: str
    leader_id: str
    graph_name: str
    graph_version: int
    phase: str
    created_agent_ids: list[str]
    result_summary: NotRequired[str]
    final_answer: NotRequired[str]


class NullWorkspaceProvisioner:
    async def provision(self, agent_id: UUID, profile: AgentProfile) -> Path | None:
        return None

    async def release(self, agent_id: UUID) -> None:
        return None


class TeamCodingGraph:
    def __init__(
        self,
        saver: AsyncPostgresSaver,
        *,
        model_resolver: RoleModelResolver,
        profiles: ProfileRegistry | None = None,
    ) -> None:
        self.saver = saver
        self.model_resolver = model_resolver
        self.profiles = profiles or ProfileRegistry()
        self._run: Run | None = None
        self._leader: Agent | None = None
        self._repository: RuntimeRepository | None = None
        self._event_sink: TeamEventSink | None = None

        builder = StateGraph(TeamCodingState)
        builder.add_node("activate_team", self._activate_team)
        builder.add_edge(START, "activate_team")
        builder.add_edge("activate_team", END)
        self.graph = builder.compile(checkpointer=saver, name=TEAM_CODING_GRAPH)

    async def execute(
        self,
        run: Run,
        leader: Agent,
        *,
        repository: RuntimeRepository,
        event_sink: TeamEventSink | None = None,
    ) -> tuple[TeamCodingState, bool]:
        self._validate_run(run, leader)
        self._run = run
        self._leader = leader
        self._repository = repository
        self._event_sink = event_sink
        config: RunnableConfig = {
            "configurable": {
                "thread_id": run.graph_thread_id,
                "checkpoint_ns": "",
            }
        }
        checkpoint = await self.saver.aget_tuple(config)
        if checkpoint is None:
            result = await self.graph.ainvoke(
                {
                    "run_id": str(run.id),
                    "leader_id": str(leader.id),
                    "graph_name": TEAM_CODING_GRAPH,
                    "graph_version": TEAM_CODING_VERSION,
                    "phase": "created",
                    "created_agent_ids": [],
                },
                config,
                durability="sync",
            )
            return _state(result), False

        snapshot = await self.graph.aget_state(config)
        if not snapshot.next:
            return _state(snapshot.values), True
        result = await self.graph.ainvoke(None, config, durability="sync")
        return _state(result), True

    async def _activate_team(self, state: TeamCodingState) -> TeamCodingState:
        run = _required(self._run, "Run")
        leader = _required(self._leader, "Leader")
        repository = _required(self._repository, "RuntimeRepository")
        team = TeamRuntime(
            run_id=run.id,
            leader=leader,
            profiles=self.profiles,
            model_resolver=self.model_resolver,
            workspace_provisioner=NullWorkspaceProvisioner(),
        )
        await team.activate(["backend-engineer", "repo-explorer"])
        backend = next(
            handle
            for handle in team.teammates.values()
            if handle.session.agent.profile == "backend-engineer"
        )
        subagent = backend.create_subagent(profile_name="repo-explorer")
        created = [
            *(handle.session.agent for handle in team.teammates.values()),
            subagent.agent,
        ]
        for agent in created:
            await repository.add_agent(agent)
            await self._emit_agent_created(agent)

        return {
            **state,
            "phase": "team_activated",
            "created_agent_ids": [str(agent.id) for agent in created],
            "result_summary": "Team runtime activated.",
            "final_answer": "Team runtime activated and awaits role execution.",
        }

    async def _emit_agent_created(self, agent: Agent) -> None:
        if self._event_sink is None:
            return
        await self._event_sink(
            EventType.AGENT_CREATED,
            {
                "agent_id": str(agent.id),
                "parent_agent_id": (
                    str(agent.parent_agent_id)
                    if agent.parent_agent_id is not None
                    else None
                ),
                "kind": agent.kind.value,
                "profile": agent.profile,
                "model": agent.model,
            },
            f"agent:create:{agent.id}",
        )

    def _validate_run(self, run: Run, leader: Agent) -> None:
        if (
            run.mode is not RunMode.TEAM
            or run.graph_name != TEAM_CODING_GRAPH
            or run.graph_version != TEAM_CODING_VERSION
        ):
            raise IncompatibleGraphError(
                f"Unsupported team graph identity: {run.graph_name}@{run.graph_version}"
            )
        if leader.kind is not AgentKind.LEADER:
            raise CorruptRuntimeStateError("Team Run requires a Leader.")
        if run.graph_thread_id is None:
            raise CorruptRuntimeStateError("Run is missing graph_thread_id.")


type TeamEventSink = Callable[[EventType, dict[str, object], str], Awaitable[None]]


def _required[T](value: T | None, name: str) -> T:
    if value is None:
        raise CorruptRuntimeStateError(f"{name} is unavailable.")
    return value


def _state(value: object) -> TeamCodingState:
    if not isinstance(value, dict):
        raise CorruptRuntimeStateError("Team graph returned invalid state.")
    required = {
        "run_id",
        "leader_id",
        "graph_name",
        "graph_version",
        "phase",
        "created_agent_ids",
    }
    if not required.issubset(value):
        raise CorruptRuntimeStateError("Team graph state is incomplete.")
    return cast(TeamCodingState, value)
