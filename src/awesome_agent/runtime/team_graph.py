from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import NotRequired, TypedDict, cast
from uuid import UUID

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from awesome_agent.agents.profiles import (
    AgentProfile,
    ProfileRegistry,
    RoleModelResolver,
)
from awesome_agent.domain.enums import AgentKind, EventType, RunMode
from awesome_agent.domain.models import Agent, Run
from awesome_agent.modeling import ToolCall, ToolResultMessage
from awesome_agent.orchestration.team import TeamRuntime
from awesome_agent.runtime.dispatch import (
    CorruptRuntimeStateError,
    IncompatibleGraphError,
)
from awesome_agent.runtime.graphs import TEAM_CODING_GRAPH, TEAM_CODING_VERSION
from awesome_agent.runtime.repository import RuntimeRepository
from awesome_agent.tools.repository import (
    build_modifying_executor,
    build_modifying_registry,
    execute_repository_call,
)

__all__ = [
    "TEAM_CODING_GRAPH",
    "TEAM_CODING_VERSION",
    "AgentAssignment",
    "TeamCodingGraph",
    "TeamCodingState",
]


class AgentAssignment(BaseModel):
    profile: str
    allowed_tools: list[str] = Field(default_factory=list)
    allowed_skills: list[str] = Field(default_factory=list)
    can_write: bool = False
    can_delegate: bool = False
    max_subagents: int = 0
    acceptance_criteria: list[str] = Field(default_factory=list)


class TeamCodingState(TypedDict):
    run_id: str
    leader_id: str
    graph_name: str
    graph_version: int
    phase: str
    created_agent_ids: list[str]
    assignments: dict[str, dict[str, object]]
    evidence: dict[str, list[dict[str, object]]]
    tool_call_count: int
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
        builder.add_node("repo_explorer_step", self._repo_explorer_step)
        builder.add_node("backend_subagent_step", self._backend_subagent_step)
        builder.add_node("backend_precheck_step", self._backend_precheck_step)
        builder.add_edge(START, "activate_team")
        builder.add_edge("activate_team", "repo_explorer_step")
        builder.add_edge("repo_explorer_step", "backend_subagent_step")
        builder.add_edge("backend_subagent_step", "backend_precheck_step")
        builder.add_edge("backend_precheck_step", END)
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
                    "assignments": {},
                    "evidence": {},
                    "tool_call_count": 0,
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
            "assignments": {
                assignment.profile: assignment.model_dump(mode="json")
                for assignment in _default_assignments()
            },
            "result_summary": "Team runtime activated.",
            "final_answer": "Team runtime activated and awaits role execution.",
        }

    async def _repo_explorer_step(
        self,
        state: TeamCodingState,
    ) -> TeamCodingState:
        run = _required(self._run, "Run")
        repository = _required(self._repository, "RuntimeRepository")
        repo_explorer = _agent_by_profile(
            await repository.list_agents(run.id),
            "repo-explorer",
            kind=AgentKind.TEAMMATE,
        )
        result = await self.execute_scoped_repository_tool(
            run=run,
            agent=repo_explorer,
            assignment=_assignment(state, "repo-explorer"),
            call=ToolCall(
                call_id="repo-explorer:status", name="repo.status", arguments_json="{}"
            ),
        )
        await self._emit_tool_call(
            repo_explorer, "repo-explorer:status", "repo.status", result
        )
        return _append_evidence(
            state,
            key="repo-explorer",
            evidence=_tool_evidence("repo.status", result),
        )

    async def _backend_subagent_step(
        self,
        state: TeamCodingState,
    ) -> TeamCodingState:
        run = _required(self._run, "Run")
        repository = _required(self._repository, "RuntimeRepository")
        subagent = next(
            agent
            for agent in await repository.list_agents(run.id)
            if agent.kind is AgentKind.SUBAGENT and agent.profile == "repo-explorer"
        )
        result = await self.execute_scoped_repository_tool(
            run=run,
            agent=subagent,
            assignment=_assignment(state, "backend-subagent"),
            call=ToolCall(
                call_id="backend-subagent:readme",
                name="repo.read",
                arguments_json='{"path":"README.md"}',
            ),
        )
        await self._emit_tool_call(
            subagent, "backend-subagent:readme", "repo.read", result
        )
        return _append_evidence(
            state,
            key="backend-subagent",
            evidence=_tool_evidence("repo.read", result),
        )

    async def _backend_precheck_step(
        self,
        state: TeamCodingState,
    ) -> TeamCodingState:
        run = _required(self._run, "Run")
        repository = _required(self._repository, "RuntimeRepository")
        backend = _agent_by_profile(
            await repository.list_agents(run.id),
            "backend-engineer",
            kind=AgentKind.TEAMMATE,
        )
        assignment = _assignment(state, "backend-engineer")
        patch_result = await self.execute_scoped_repository_tool(
            run=run,
            agent=backend,
            assignment=assignment,
            call=ToolCall(
                call_id="backend:patch",
                name="repo.apply_patch",
                arguments_json=(
                    '{"patch":"diff --git a/README.md b/README.md\\n'
                    "--- a/README.md\\n"
                    "+++ b/README.md\\n"
                    "@@ -1 +1,2 @@\\n"
                    " fixture\\n"
                    '+team runtime update\\n"}'
                ),
            ),
        )
        await self._emit_tool_call(
            backend,
            "backend:patch",
            "repo.apply_patch",
            patch_result,
        )
        diff_result = await self.execute_scoped_repository_tool(
            run=run,
            agent=backend,
            assignment=assignment,
            call=ToolCall(
                call_id="backend:diff",
                name="repo.diff",
                arguments_json="{}",
            ),
        )
        await self._emit_tool_call(backend, "backend:diff", "repo.diff", diff_result)
        updated = _append_evidence(
            state,
            key="backend-engineer",
            evidence=_tool_evidence("repo.apply_patch", patch_result),
        )
        updated = _append_evidence(
            updated,
            key="backend-engineer",
            evidence=_tool_evidence("repo.diff", diff_result),
        )
        return {
            **updated,
            "phase": "role_steps_completed",
            "result_summary": "Team runtime completed bounded role steps.",
            "final_answer": "Team roles produced initial repository evidence.",
        }

    async def execute_scoped_repository_tool(
        self,
        *,
        run: Run,
        agent: Agent,
        assignment: AgentAssignment,
        call: ToolCall,
    ) -> ToolResultMessage:
        if call.name not in assignment.allowed_tools:
            return ToolResultMessage(
                call_id=call.call_id,
                content=f"Tool {call.name} is not allowed for {assignment.profile}.",
                is_error=True,
            )
        if run.workspace_path is None:
            raise CorruptRuntimeStateError("Run workspace is unavailable.")
        capabilities = {"repository:read"}
        if assignment.can_write:
            capabilities.add("repository:write")
        registry = build_modifying_registry()
        executor = build_modifying_executor(registry)
        return await execute_repository_call(
            executor,
            call,
            workspace=run.workspace_path,
            agent_id=agent.id,
            profile=agent.profile,
            capabilities=capabilities,
        )

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

    async def _emit_tool_call(
        self,
        agent: Agent,
        call_id: str,
        tool_name: str,
        result: ToolResultMessage,
    ) -> None:
        if self._event_sink is None:
            return
        await self._event_sink(
            EventType.TOOL_CALL_CREATED,
            {
                "agent_id": str(agent.id),
                "profile": agent.profile,
                "kind": agent.kind.value,
                "call_id": call_id,
                "tool": tool_name,
                "status": "failed" if result.is_error else "completed",
                "error": result.content if result.is_error else "",
            },
            f"tool:{agent.id}:{call_id}",
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
        "assignments",
        "evidence",
        "tool_call_count",
    }
    if not required.issubset(value):
        raise CorruptRuntimeStateError("Team graph state is incomplete.")
    return cast(TeamCodingState, value)


def _default_assignments() -> list[AgentAssignment]:
    read_tools = [
        "repo.status",
        "repo.list",
        "repo.search",
        "repo.read",
        "repo.instructions",
        "artifact.read",
    ]
    return [
        AgentAssignment(
            profile="backend-engineer",
            allowed_tools=[
                "repo.status",
                "repo.list",
                "repo.search",
                "repo.read",
                "repo.instructions",
                "repo.diff",
                "repo.apply_patch",
                "artifact.read",
            ],
            allowed_skills=["repository-inspection", "patch-authoring"],
            can_write=True,
            can_delegate=True,
            max_subagents=1,
            acceptance_criteria=[
                "Modify only through authorized repository tools.",
                "Inspect the final diff before submitting.",
            ],
        ),
        AgentAssignment(
            profile="repo-explorer",
            allowed_tools=read_tools,
            allowed_skills=["repository-inspection"],
            can_write=False,
            can_delegate=False,
            max_subagents=0,
            acceptance_criteria=["Gather bounded repository evidence."],
        ),
        AgentAssignment(
            profile="backend-subagent",
            allowed_tools=read_tools,
            allowed_skills=["bounded-evidence-gathering"],
            can_write=False,
            can_delegate=False,
            max_subagents=0,
            acceptance_criteria=["Return read-only evidence to backend-engineer."],
        ),
        AgentAssignment(
            profile="verifier",
            allowed_tools=[
                "repo.status",
                "repo.list",
                "repo.search",
                "repo.read",
                "repo.instructions",
                "repo.diff",
                "artifact.read",
            ],
            allowed_skills=["verification-review"],
            can_write=False,
            can_delegate=True,
            max_subagents=1,
            acceptance_criteria=["Verify implementation evidence independently."],
        ),
    ]


def _assignment(state: TeamCodingState, profile: str) -> AgentAssignment:
    return AgentAssignment.model_validate(state["assignments"][profile])


def _agent_by_profile(
    agents: list[Agent],
    profile: str,
    *,
    kind: AgentKind,
) -> Agent:
    return next(
        agent for agent in agents if agent.profile == profile and agent.kind is kind
    )


def _tool_evidence(tool_name: str, result: ToolResultMessage) -> dict[str, object]:
    return {
        "tool": tool_name,
        "status": "failed" if result.is_error else "completed",
        "content": result.content[:1000],
    }


def _append_evidence(
    state: TeamCodingState,
    *,
    key: str,
    evidence: dict[str, object],
) -> TeamCodingState:
    current = {name: list(items) for name, items in state["evidence"].items()}
    current.setdefault(key, []).append(evidence)
    return {
        **state,
        "evidence": current,
        "tool_call_count": state["tool_call_count"] + 1,
    }
