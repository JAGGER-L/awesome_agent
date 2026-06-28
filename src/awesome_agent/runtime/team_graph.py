from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
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
from awesome_agent.domain.enums import AgentKind, EventType, RunMode, TodoStatus
from awesome_agent.domain.models import Agent, Run, TodoItem
from awesome_agent.modeling import (
    ModelProvider,
    ModelRequest,
    SystemMessage,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from awesome_agent.orchestration.team import TeamRuntime
from awesome_agent.persistence.budget import BudgetRepository, RunBudgetLedgerRecord
from awesome_agent.persistence.tool_invocations import (
    DurableToolInvocation,
    ToolInvocationRepository,
)
from awesome_agent.persistence.validation import (
    DurableValidationGateResult,
    DurableValidationReport,
    ValidationRepository,
)
from awesome_agent.runtime.budget import (
    BudgetDecision,
    BudgetLedger,
    BudgetPolicy,
    TokenUsageDelta,
    estimate_messages_tokens,
    evaluate_budget,
)
from awesome_agent.runtime.dispatch import (
    CorruptRuntimeStateError,
    IncompatibleGraphError,
    PermanentExecutionError,
)
from awesome_agent.runtime.graphs import (
    SCOPED_TEAM_CODING_GRAPH,
)
from awesome_agent.runtime.repository import RuntimeRepository
from awesome_agent.tools.repository import (
    build_modifying_executor,
    build_modifying_registry,
    canonical_arguments_hash,
    execute_repository_call,
    repository_tool_effect_metadata,
    tool_invocation_uuid,
)

__all__ = [
    "SCOPED_TEAM_CODING_GRAPH",
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
    phase: str
    created_agent_ids: list[str]
    assignments: dict[str, dict[str, object]]
    evidence: dict[str, list[dict[str, object]]]
    tool_call_count: int
    budget_ledger: NotRequired[dict[str, object]]
    backend_todo_id: NotRequired[str]
    verification_rework_count: int
    verification_reports: list[dict[str, object]]
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
        provider_resolver: ProviderResolver | None = None,
        validation_repository: ValidationRepository | None = None,
        tool_repository: ToolInvocationRepository | None = None,
        verification_outcomes: list[str] | None = None,
        max_verification_reworks: int = 10,
        max_verification_execution_retries: int = 1,
        budget_repository: BudgetRepository | None = None,
        budget_policy: BudgetPolicy | None = None,
    ) -> None:
        self.saver = saver
        self.model_resolver = model_resolver
        self.profiles = profiles or ProfileRegistry()
        self.provider_resolver = provider_resolver
        self.validation_repository = validation_repository
        self.tool_repository = tool_repository
        self.verification_outcomes = verification_outcomes or ["failed", "passed"]
        self.max_verification_reworks = max_verification_reworks
        self.max_verification_execution_retries = max_verification_execution_retries
        self.budget_repository = budget_repository
        self.budget_policy = budget_policy
        self._run: Run | None = None
        self._leader: Agent | None = None
        self._repository: RuntimeRepository | None = None
        self._event_sink: TeamEventSink | None = None

        builder = StateGraph(TeamCodingState)
        builder.add_node("activate_team", self._activate_team)
        builder.add_node("repo_explorer_step", self._repo_explorer_step)
        builder.add_node("backend_subagent_step", self._backend_subagent_step)
        builder.add_node("backend_precheck_step", self._backend_precheck_step)
        builder.add_node("verify", self._verify)
        builder.add_edge(START, "activate_team")
        builder.add_edge("activate_team", "repo_explorer_step")
        builder.add_edge("repo_explorer_step", "backend_subagent_step")
        builder.add_edge("backend_subagent_step", "backend_precheck_step")
        builder.add_edge("backend_precheck_step", "verify")
        builder.add_edge("verify", END)
        self.graph = builder.compile(checkpointer=saver, name=SCOPED_TEAM_CODING_GRAPH)

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
                    "graph_name": SCOPED_TEAM_CODING_GRAPH,
                    "phase": "created",
                    "created_agent_ids": [],
                    "assignments": {},
                    "evidence": {},
                    "tool_call_count": 0,
                    "budget_ledger": {},
                    "verification_rework_count": 0,
                    "verification_reports": [],
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
        await self._model_step(
            leader,
            purpose="Plan team assignments and verification responsibilities.",
            turn=1,
        )
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
        todo = TodoItem(
            run_id=run.id,
            title="Backend implementation",
            description=run.goal,
            status=TodoStatus.IN_PROGRESS,
            primary_owner_id=backend.session.agent.id,
            acceptance_criteria=[
                "Apply changes only through authorized tools.",
                "Verifier must accept the result before Leader completion.",
            ],
        )
        await repository.add_todo(todo)
        await self._emit_todo_created(todo)

        return {
            **state,
            "phase": "team_activated",
            "created_agent_ids": [str(agent.id) for agent in created],
            "backend_todo_id": str(todo.id),
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
        await self._model_step(
            repo_explorer,
            purpose="Inspect repository status for the team.",
            turn=1,
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
        await self._model_step(
            subagent,
            purpose="Gather bounded README evidence for backend-engineer.",
            turn=1,
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
        await self._model_step(
            backend,
            purpose="Implement the assigned backend change with scoped tools.",
            turn=1,
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

    async def _verify(self, state: TeamCodingState) -> TeamCodingState:
        run = _required(self._run, "Run")
        repository = _required(self._repository, "RuntimeRepository")
        verifier = _agent_by_profile(
            await repository.list_agents(run.id),
            "verifier",
            kind=AgentKind.VERIFIER,
        )
        backend = _agent_by_profile(
            await repository.list_agents(run.id),
            "backend-engineer",
            kind=AgentKind.TEAMMATE,
        )
        todo = _backend_todo(await repository.list_todos(run.id), state)
        reports = list(state["verification_reports"])
        rework_count = state["verification_rework_count"]
        attempt = len(reports)
        current = state
        while True:
            await self._transition_todo(
                repository,
                todo,
                TodoStatus.SUBMITTED,
                reason="backend submitted work",
            )
            await self._transition_todo(
                repository,
                todo,
                TodoStatus.VERIFYING,
                reason="verifier reviewing work",
            )
            outcome = self.verification_outcomes[
                min(attempt, len(self.verification_outcomes) - 1)
            ]
            await self._model_step(
                verifier,
                purpose="Independently verify teammate evidence.",
                turn=attempt + 1,
            )
            report = await self._record_verification_report(
                run=run,
                verifier=verifier,
                attempt=attempt + 1,
                status="passed" if outcome == "passed" else "failed",
                summary=(
                    "Verification passed."
                    if outcome == "passed"
                    else "Verification rejected implementation evidence."
                ),
            )
            reports.append(
                {
                    "id": str(report.id),
                    "attempt": report.attempt,
                    "status": report.status,
                    "summary": report.summary,
                }
            )
            await self._emit_verification(verifier, report)
            if outcome == "passed":
                await self._transition_todo(
                    repository,
                    todo,
                    TodoStatus.VERIFIED,
                    reason="verification passed",
                )
                await self._transition_todo(
                    repository,
                    todo,
                    TodoStatus.DONE,
                    reason="leader accepted verified work",
                )
                return {
                    **current,
                    "phase": "verified",
                    "verification_rework_count": rework_count,
                    "verification_reports": reports,
                    "result_summary": "Team implementation verified.",
                    "final_answer": "Team completed after verifier approval.",
                }
            if rework_count >= self.max_verification_reworks:
                raise PermanentExecutionError("verification_rejected_limit_exceeded")
            await self._transition_todo(
                repository,
                todo,
                TodoStatus.REJECTED,
                reason="verification rejected implementation",
            )
            await self._transition_todo(
                repository,
                todo,
                TodoStatus.IN_PROGRESS,
                reason="backend rework requested",
            )
            current = await self._backend_rework_step(current, backend)
            rework_count += 1
            attempt += 1

    async def _backend_rework_step(
        self,
        state: TeamCodingState,
        backend: Agent,
    ) -> TeamCodingState:
        run = _required(self._run, "Run")
        assignment = _assignment(state, "backend-engineer")
        await self._model_step(
            backend,
            purpose="Rework implementation after verifier rejection.",
            turn=state["verification_rework_count"] + 2,
        )
        patch_result = await self.execute_scoped_repository_tool(
            run=run,
            agent=backend,
            assignment=assignment,
            call=ToolCall(
                call_id=f"backend:rework:{state['verification_rework_count'] + 1}",
                name="repo.apply_patch",
                arguments_json=(
                    '{"patch":"diff --git a/README.md b/README.md\\n'
                    "--- a/README.md\\n"
                    "+++ b/README.md\\n"
                    "@@ -1,2 +1,3 @@\\n"
                    " fixture\\n"
                    " team runtime update\\n"
                    '+team runtime rework\\n"}'
                ),
            ),
        )
        await self._emit_tool_call(
            backend,
            f"backend:rework:{state['verification_rework_count'] + 1}",
            "repo.apply_patch",
            patch_result,
        )
        return _append_evidence(
            state,
            key="backend-engineer",
            evidence=_tool_evidence("repo.apply_patch", patch_result),
        )

    async def execute_scoped_repository_tool(
        self,
        *,
        run: Run,
        agent: Agent,
        assignment: AgentAssignment,
        call: ToolCall,
    ) -> ToolResultMessage:
        if call.name not in assignment.allowed_tools:
            result = ToolResultMessage(
                call_id=call.call_id,
                content=f"Tool {call.name} is not allowed for {assignment.profile}.",
                is_error=True,
            )
            await self._record_tool_invocation(run, agent, call, result)
            return result
        if run.workspace_path is None:
            raise CorruptRuntimeStateError("Run workspace is unavailable.")
        capabilities = {"repository:read"}
        if assignment.can_write:
            capabilities.add("repository:write")
        registry = build_modifying_registry()
        executor = build_modifying_executor(registry)
        result = await execute_repository_call(
            executor,
            call,
            workspace=run.workspace_path,
            agent_id=agent.id,
            profile=agent.profile,
            capabilities=capabilities,
        )
        await self._record_tool_invocation(run, agent, call, result)
        return result

    async def _record_tool_invocation(
        self,
        run: Run,
        agent: Agent,
        call: ToolCall,
        result: ToolResultMessage,
    ) -> None:
        if self.tool_repository is None:
            return
        arguments_hash = canonical_arguments_hash(call)
        path_refs: list[str] = []
        preimage_hashes: dict[str, str] = {}
        if run.workspace_path is not None and call.name == "repo.apply_patch":
            from awesome_agent.tools.repository import parse_tool_call_arguments

            path_refs, preimage_hashes = repository_tool_effect_metadata(
                call.name,
                parse_tool_call_arguments(call),
                workspace=run.workspace_path,
            )
        await self.tool_repository.upsert(
            DurableToolInvocation(
                id=tool_invocation_uuid(f"{run.id}:team:{agent.id}:{call.call_id}"),
                run_id=run.id,
                agent_id=agent.id,
                tool_name=call.name,
                tool_version="1",
                status=(
                    "denied"
                    if "not allowed" in result.content
                    else "failed"
                    if result.is_error
                    else "completed"
                ),
                idempotency_key=(
                    f"team:{run.id}:{agent.id}:{call.name}:{arguments_hash}"
                ),
                arguments_hash=arguments_hash,
                risk_level="medium" if call.name == "repo.apply_patch" else "low",
                path_refs=path_refs,
                preimage_hashes=preimage_hashes,
                result_content=result.content,
                result_is_error=result.is_error,
                error=result.content if result.is_error else None,
            )
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

    async def _model_step(self, agent: Agent, *, purpose: str, turn: int) -> None:
        if self.provider_resolver is None:
            return
        run = _required(self._run, "Run")
        started = monotonic()
        provider = self.provider_resolver(agent.model)
        request = ModelRequest(
            messages=[
                SystemMessage(
                    content=(
                        "You are a bounded role inside a team coding runtime. "
                        "Acknowledge the assigned step; tool execution is "
                        "controlled by the runtime."
                    )
                ),
                UserMessage(content=purpose),
            ],
            max_output_tokens=200,
        )
        ledger = await self._evaluate_budget_before_model_call(
            run_id=run.id,
            agent=agent,
            request=request,
            turn=turn,
        )
        model_turn = await provider.complete(request)
        ledger = ledger.add_usage(
            TokenUsageDelta(
                input_tokens=model_turn.usage.input_tokens or 0,
                output_tokens=model_turn.usage.output_tokens or 0,
                reasoning_tokens=model_turn.usage.reasoning_tokens or 0,
            )
        )
        await self._persist_budget_ledger(run.id, ledger)
        if self._event_sink is None:
            return
        usage = model_turn.usage
        await self._event_sink(
            EventType.MODEL_CALL_CREATED,
            {
                "agent_id": str(agent.id),
                "profile": agent.profile,
                "kind": agent.kind.value,
                "turn": turn,
                "status": "completed",
                "provider": model_turn.provider,
                "model": model_turn.model,
                "stop_reason": model_turn.stop_reason.value,
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "reasoning_tokens": usage.reasoning_tokens,
                "cache_read_tokens": usage.cache_read_tokens,
                "cache_write_tokens": usage.cache_write_tokens,
                "latency_ms": int((monotonic() - started) * 1000),
            },
            f"model:{agent.id}:{turn}",
        )

    async def _evaluate_budget_before_model_call(
        self,
        *,
        run_id: UUID,
        agent: Agent,
        request: ModelRequest,
        turn: int,
    ) -> BudgetLedger:
        ledger = await self._load_budget_ledger(run_id)
        if self.budget_policy is None:
            return ledger
        now = datetime.now(UTC)
        estimated_prompt_tokens = estimate_messages_tokens(request.messages)
        decision = evaluate_budget(
            ledger,
            self.budget_policy,
            estimated_prompt_tokens=estimated_prompt_tokens,
            now=now,
        )
        updated = replace(ledger, threshold_status=decision.value)
        await self._persist_budget_ledger(run_id, updated)
        if decision is BudgetDecision.EXHAUSTED:
            exhausted = replace(
                updated,
                threshold_status=BudgetDecision.EXHAUSTED.value,
            )
            await self._persist_budget_ledger(run_id, exhausted)
            if self._event_sink is not None:
                await self._event_sink(
                    EventType.BUDGET_EXHAUSTED,
                    {
                        "agent_id": str(agent.id),
                        "profile": agent.profile,
                        "turn": turn,
                        "estimated_prompt_tokens": estimated_prompt_tokens,
                        "total_tokens": exhausted.total_tokens,
                        "reasoning_tokens": exhausted.total_reasoning_tokens,
                        "active_seconds": exhausted.active_seconds_at(now),
                    },
                    f"budget-exhausted:{agent.id}:{turn}",
                )
            raise PermanentExecutionError(
                "budget_exhausted: token or wall-clock budget exhausted"
            )
        if (
            decision in {BudgetDecision.COMPACT, BudgetDecision.FINAL_ANSWER}
            and self._event_sink is not None
        ):
            await self._event_sink(
                EventType.BUDGET_THRESHOLD_REACHED,
                {
                    "agent_id": str(agent.id),
                    "profile": agent.profile,
                    "turn": turn,
                    "decision": decision.value,
                    "estimated_prompt_tokens": estimated_prompt_tokens,
                },
                f"budget-threshold:{agent.id}:{turn}",
            )
        return updated

    async def _load_budget_ledger(self, run_id: UUID) -> BudgetLedger:
        if self.budget_repository is None:
            return BudgetLedger()
        record = await self.budget_repository.get_ledger(run_id)
        return _ledger_from_record(record)

    async def _persist_budget_ledger(
        self,
        run_id: UUID,
        ledger: BudgetLedger,
    ) -> None:
        if self.budget_repository is None:
            return
        await self.budget_repository.upsert_ledger(_record_from_ledger(run_id, ledger))

    async def _emit_todo_created(self, todo: TodoItem) -> None:
        if self._event_sink is None:
            return
        await self._event_sink(
            EventType.TODO_CREATED,
            {
                "todo_id": str(todo.id),
                "title": todo.title,
                "status": todo.status.value,
                "primary_owner_id": (
                    str(todo.primary_owner_id)
                    if todo.primary_owner_id is not None
                    else None
                ),
            },
            f"todo:create:{todo.id}",
        )

    async def _transition_todo(
        self,
        repository: RuntimeRepository,
        todo: TodoItem,
        status: TodoStatus,
        *,
        reason: str,
    ) -> None:
        updated = todo.model_copy(
            update={
                "status": status,
                "revision": todo.revision + 1,
            }
        )
        todo.status = updated.status
        todo.revision = updated.revision
        await repository.update_todo(updated)
        if self._event_sink is None:
            return
        await self._event_sink(
            EventType.TODO_STATUS_CHANGED,
            {
                "todo_id": str(todo.id),
                "status": status.value,
                "reason": reason,
                "revision": updated.revision,
            },
            f"todo:{todo.id}:{status.value}:{updated.revision}",
        )

    async def _record_verification_report(
        self,
        *,
        run: Run,
        verifier: Agent,
        attempt: int,
        status: str,
        summary: str,
    ) -> DurableValidationReport:
        report = DurableValidationReport(
            run_id=run.id,
            agent_id=verifier.id,
            attempt=attempt,
            status=status,
            summary=summary,
        )
        gate = DurableValidationGateResult(
            report_id=report.id,
            run_id=run.id,
            gate_id="team-verifier",
            name="Team verifier review",
            command=["team-verifier"],
            required=True,
            status=status,
            exit_code=0 if status == "passed" else 1,
            failure_kind=None if status == "passed" else "verification_rejected",
            stdout_summary=summary,
        )
        if self.validation_repository is not None:
            await self.validation_repository.record_report(report, gates=[gate])
        return report

    async def _emit_verification(
        self,
        verifier: Agent,
        report: DurableValidationReport,
    ) -> None:
        if self._event_sink is None:
            return
        await self._event_sink(
            EventType.VERIFICATION_CREATED,
            {
                "verification_report_id": str(report.id),
                "agent_id": str(verifier.id),
                "attempt": report.attempt,
                "status": report.status,
                "summary": report.summary,
            },
            f"verification:{report.id}",
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
            or run.graph_name != SCOPED_TEAM_CODING_GRAPH
        ):
            raise IncompatibleGraphError(
                f"Unsupported team graph identity: {run.graph_name}"
            )
        if leader.kind is not AgentKind.LEADER:
            raise CorruptRuntimeStateError("Team Run requires a Leader.")
        if run.graph_thread_id is None:
            raise CorruptRuntimeStateError("Run is missing graph_thread_id.")


type TeamEventSink = Callable[[EventType, dict[str, object], str], Awaitable[None]]
type ProviderResolver = Callable[[str], ModelProvider]


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
        "phase",
        "created_agent_ids",
        "assignments",
        "evidence",
        "tool_call_count",
        "verification_rework_count",
        "verification_reports",
    }
    if not required.issubset(value):
        raise CorruptRuntimeStateError("Team graph state is incomplete.")
    return cast(TeamCodingState, value)


def _ledger_from_record(record: RunBudgetLedgerRecord) -> BudgetLedger:
    return BudgetLedger(
        total_input_tokens=record.total_input_tokens,
        total_output_tokens=record.total_output_tokens,
        total_reasoning_tokens=record.total_reasoning_tokens,
        active_seconds=record.active_seconds,
        model_call_count=record.model_call_count,
        threshold_status=record.threshold_status,
        active_window_started_at=record.active_window_started_at,
    )


def _record_from_ledger(
    run_id: UUID,
    ledger: BudgetLedger,
) -> RunBudgetLedgerRecord:
    return RunBudgetLedgerRecord(
        run_id=run_id,
        total_input_tokens=ledger.total_input_tokens,
        total_output_tokens=ledger.total_output_tokens,
        total_reasoning_tokens=ledger.total_reasoning_tokens,
        active_seconds=ledger.active_seconds,
        model_call_count=ledger.model_call_count,
        threshold_status=ledger.threshold_status,
        active_window_started_at=ledger.active_window_started_at,
    )


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


def _backend_todo(todos: list[TodoItem], state: TeamCodingState) -> TodoItem:
    todo_id = state.get("backend_todo_id")
    if not isinstance(todo_id, str):
        raise CorruptRuntimeStateError("Team graph has no backend todo.")
    return next(todo for todo in todos if str(todo.id) == todo_id)
