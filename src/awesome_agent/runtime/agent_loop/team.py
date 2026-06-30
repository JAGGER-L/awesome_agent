from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import TypeVar

from awesome_agent.domain.models import Agent, Run
from awesome_agent.modeling import ModelMessage
from awesome_agent.observability.facade import ObservabilityFacade
from awesome_agent.runtime.agent_loop.contracts import (
    AssignmentContext,
    CapabilityContext,
    HandoffContext,
    MiddlewareContext,
    MiddlewareStage,
    TokenBudgetContext,
    TraceContext,
)
from awesome_agent.runtime.agent_loop.middleware import MiddlewareStack
from awesome_agent.runtime.agent_loop.observability_middleware import (
    ObservabilityMiddleware,
)

StateT = TypeVar("StateT")
ResultT = TypeVar("ResultT")

_SENSITIVE_METADATA_FRAGMENTS = frozenset(
    {
        "api_key",
        "authorization",
        "continuation",
        "header",
        "message",
        "patch",
        "prompt",
        "secret",
        "tool_result",
        "verifier_json",
    }
)


class TeamAgentLoop:
    def __init__(
        self,
        *,
        middleware_stack: MiddlewareStack | None = None,
        observability: ObservabilityFacade | None = None,
    ) -> None:
        middleware = list(middleware_stack.middleware) if middleware_stack else []
        if observability is not None:
            middleware.append(ObservabilityMiddleware(observability))
        self.middleware_stack = MiddlewareStack(middleware)

    async def run_agent_operation(
        self,
        state: StateT,
        *,
        run: Run,
        agent: Agent,
        messages: Sequence[ModelMessage],
        handler: Callable[[StateT], Awaitable[ResultT]],
        assignment_id: object | None = None,
        team_role: str | None = None,
        agent_kind: str | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> ResultT:
        return await self._run_stage(
            MiddlewareStage.BEFORE_AGENT,
            state,
            run=run,
            agent=agent,
            messages=messages,
            handler=handler,
            assignment_id=assignment_id,
            team_role=team_role,
            agent_kind=agent_kind,
            metadata=metadata,
        )

    async def wrap_model_call(
        self,
        state: StateT,
        *,
        run: Run,
        agent: Agent,
        messages: Sequence[ModelMessage],
        handler: Callable[[StateT], Awaitable[ResultT]],
        assignment_id: object | None = None,
        team_role: str | None = None,
        agent_kind: str | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> ResultT:
        return await self._run_stage(
            MiddlewareStage.WRAP_MODEL_CALL,
            state,
            run=run,
            agent=agent,
            messages=messages,
            handler=handler,
            assignment_id=assignment_id,
            team_role=team_role,
            agent_kind=agent_kind,
            metadata=metadata,
        )

    async def wrap_tool_call(
        self,
        state: StateT,
        *,
        run: Run,
        agent: Agent,
        messages: Sequence[ModelMessage],
        handler: Callable[[StateT], Awaitable[ResultT]],
        assignment_id: object | None = None,
        team_role: str | None = None,
        agent_kind: str | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> ResultT:
        return await self._run_stage(
            MiddlewareStage.WRAP_TOOL_CALL,
            state,
            run=run,
            agent=agent,
            messages=messages,
            handler=handler,
            assignment_id=assignment_id,
            team_role=team_role,
            agent_kind=agent_kind,
            metadata=metadata,
        )

    async def _run_stage(
        self,
        stage: MiddlewareStage,
        state: StateT,
        *,
        run: Run,
        agent: Agent,
        messages: Sequence[ModelMessage],
        handler: Callable[[StateT], Awaitable[ResultT]],
        assignment_id: object | None,
        team_role: str | None,
        agent_kind: str | None,
        metadata: Mapping[str, object] | None,
    ) -> ResultT:
        safe_metadata = _team_metadata(
            run=run,
            assignment_id=assignment_id,
            team_role=team_role,
            agent_kind=agent_kind,
            metadata=metadata,
        )
        context = MiddlewareContext(
            run_id=str(run.id),
            agent_id=str(agent.id),
            runtime_route=run.runtime_route or "",
            messages=list(messages),
            metadata=safe_metadata,
            trace=TraceContext(
                run_id=str(run.id),
                parent_run_id=str(run.parent_run_id) if run.parent_run_id else None,
                trace_id=str(run.root_run_id or run.id),
                runtime_route=run.runtime_route or "",
            ),
            capabilities=CapabilityContext(
                subject_id=str(agent.id),
                subject_kind=agent_kind or agent.kind.value,
                policy_id=None,
                allowed_tool_names=_string_tuple(safe_metadata.get("allowed_tools")),
                denied_tool_names=_string_tuple(safe_metadata.get("denied_tools")),
            ),
            assignment=AssignmentContext(
                assignment_id=str(assignment_id) if assignment_id is not None else None,
                leader_run_id=str(run.root_run_id or run.id),
                role=team_role,
                objective=run.goal,
            ),
            budget=TokenBudgetContext(token_limit=None),
            handoff=HandoffContext(
                handoff_id=str(assignment_id) if assignment_id is not None else None,
                source_agent=(
                    str(run.parent_run_id) if run.parent_run_id is not None else None
                ),
                target_agent=str(run.id),
                reason=_string_or_none(safe_metadata.get("team_operation")),
            ),
        )

        async def operation() -> ResultT:
            decision = await self.middleware_stack.run_stage(stage, context)
            if not decision.continue_loop:
                reason = decision.reason or f"{stage.value} stopped the team loop"
                raise RuntimeError(reason)
            return await handler(state)

        return await self.middleware_stack.run_operation(stage, context, operation)


def _team_metadata(
    *,
    run: Run,
    assignment_id: object | None,
    team_role: str | None,
    agent_kind: str | None,
    metadata: Mapping[str, object] | None,
) -> dict[str, object]:
    safe = _safe_metadata(metadata)
    safe.update(
        {
            "run_id": str(run.id),
            "run.id": str(run.id),
            "runtime_route": run.runtime_route or "",
            "runtime.route": run.runtime_route or "",
            "team_root_run_id": str(run.root_run_id or run.id),
            "team.root_run_id": str(run.root_run_id or run.id),
        }
    )
    if run.parent_run_id is not None:
        safe["parent_run_id"] = str(run.parent_run_id)
        safe["parent_run.id"] = str(run.parent_run_id)
    if assignment_id is not None:
        safe["assignment_id"] = str(assignment_id)
        safe["assignment.id"] = str(assignment_id)
    if team_role:
        safe["team_role"] = team_role
        safe["agent.role"] = team_role
    if agent_kind:
        safe["agent_kind"] = agent_kind
        safe["agent.kind"] = agent_kind
    return safe


def _safe_metadata(metadata: Mapping[str, object] | None) -> dict[str, object]:
    if metadata is None:
        return {}
    safe: dict[str, object] = {}
    for key, value in metadata.items():
        lowered = key.lower()
        if any(fragment in lowered for fragment in _SENSITIVE_METADATA_FRAGMENTS):
            continue
        safe[key] = value
    return safe


def _string_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Sequence):
        return tuple(item for item in value if isinstance(item, str))
    return ()


def _string_or_none(value: object) -> str | None:
    return value if isinstance(value, str) else None
