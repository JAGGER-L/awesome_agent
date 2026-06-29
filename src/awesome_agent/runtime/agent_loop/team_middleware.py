from __future__ import annotations

from collections.abc import Callable

from pydantic import ValidationError

from awesome_agent.domain.enums import EventType, RunIntent
from awesome_agent.domain.models import Agent, Run
from awesome_agent.modeling import (
    ModelMessage,
    ModelProvider,
    ModelProviderError,
    ModelRequest,
    ModelTurn,
    SystemMessage,
    TransientModelError,
    UserMessage,
)
from awesome_agent.runtime.agent_loop.team import TeamAgentLoop
from awesome_agent.runtime.dispatch import (
    PermanentExecutionError,
    TransientExecutionError,
)
from awesome_agent.runtime.team_planning import (
    TeamPlan,
    validate_team_plan_for_intent,
)

ProviderResolver = Callable[[str], ModelProvider]
_TEAM_PLAN_MAX_ATTEMPTS = 2


class TeamPlanningMiddleware:
    def __init__(
        self,
        *,
        provider_resolver: ProviderResolver | None,
        team_loop: TeamAgentLoop,
    ) -> None:
        self.provider_resolver = provider_resolver
        self.team_loop = team_loop

    async def create_team_plan(
        self,
        run: Run,
        leader: Agent,
        *,
        event_sink: object | None,
    ) -> tuple[TeamPlan, int]:
        if self.provider_resolver is None:
            raise PermanentExecutionError("team_plan_provider_unavailable")
        provider = self.provider_resolver(leader.model)
        messages = _initial_team_plan_messages(run)
        last_error = ""
        for attempt in range(1, _TEAM_PLAN_MAX_ATTEMPTS + 1):
            attempt_messages = list(messages)

            async def complete_plan_attempt(
                _: object,
                *,
                current_messages: list[ModelMessage] = attempt_messages,
            ) -> ModelTurn:
                try:
                    return await provider.complete(
                        ModelRequest(
                            messages=current_messages,
                            tools=[],
                        )
                    )
                except TransientModelError as error:
                    raise TransientExecutionError(str(error)) from error
                except ModelProviderError as error:
                    raise PermanentExecutionError(str(error)) from error

            turn = await self.team_loop.wrap_model_call(
                object(),
                run=run,
                agent=leader,
                messages=attempt_messages,
                team_role="leader",
                agent_kind=leader.kind.value,
                metadata={"team_operation": "planning", "attempt": attempt},
                handler=complete_plan_attempt,
            )
            try:
                plan = validate_team_plan_for_intent(
                    TeamPlan.model_validate_json(turn.assistant.content),
                    intent=run.intent,
                )
            except (ValidationError, ValueError) as error:
                last_error = str(error)
                await _emit_if_callable(
                    event_sink,
                    EventType.TEAM_PLAN_REJECTED,
                    {
                        "run_id": str(run.id),
                        "agent_id": str(leader.id),
                        "attempt": attempt,
                        "error": last_error[:2000],
                    },
                    f"team-plan-rejected:{attempt}",
                )
                if attempt >= _TEAM_PLAN_MAX_ATTEMPTS:
                    raise PermanentExecutionError(
                        f"team_plan_invalid: {last_error[:500]}"
                    ) from error
                messages = [
                    *messages,
                    turn.assistant,
                    UserMessage(
                        content=(
                            "Your previous TeamPlan was rejected. Fix these "
                            "validation errors and return only corrected JSON: "
                            f"{last_error[:2000]}"
                        )
                    ),
                ]
                continue
            await _emit_if_callable(
                event_sink,
                EventType.TEAM_PLAN_CREATED,
                {
                    "run_id": str(run.id),
                    "agent_id": str(leader.id),
                    "attempt": attempt,
                    "teammate_count": len(plan.teammates),
                    "rationale": plan.rationale[:2000],
                },
                "team-plan-created",
            )
            return plan, attempt
        raise PermanentExecutionError(f"team_plan_invalid: {last_error[:500]}")


def _initial_team_plan_messages(run: Run) -> list[ModelMessage]:
    intent_rules = (
        "The root run is read-only. Every teammate must set can_write=false and "
        "must not receive write tools."
        if run.intent is RunIntent.READ_ONLY
        else "The root run may modify files. Grant write tools only when the "
        "teammate goal truly needs file changes or shell execution."
    )
    return [
        SystemMessage(
            content=(
                "You are the Leader planning a coding-agent team. Return only "
                "valid JSON matching this schema: "
                "{"
                '"rationale":"short reason",'
                '"teammates":[{'
                '"role_profile":"lowercase-slug",'
                '"goal":"specific teammate task",'
                '"allowed_tools":["repo.status"],'
                '"deferred_tools":[],'
                '"allowed_skills":[],'
                '"can_write":false,'
                '"can_delegate":false,'
                '"max_subagents":0,'
                '"acceptance_criteria":["observable completion criterion"]'
                "}]"
                "}. Create 1 to 3 teammates. Do not create, name, describe, "
                "or direct Verifier agents. Do not include subagent_goals, "
                "delegation_guidance, or any Subagent task description. You may "
                "only set can_delegate and max_subagents for a teammate."
            )
        ),
        UserMessage(
            content=(
                f"Root goal: {run.goal}\n"
                f"Root intent: {run.intent.value}\n"
                f"{intent_rules}\n"
                "Known tools: repo.status, repo.list, repo.search, repo.read, "
                "repo.instructions, repo.diff, repo.apply_patch, shell.execute, "
                "team.create_subagent.\n"
                "Prefer the smallest useful team."
            )
        ),
    ]


async def _emit_if_callable(
    event_sink: object | None,
    event_type: EventType,
    payload: dict[str, object],
    transition_id: str,
) -> None:
    if callable(event_sink):
        await event_sink(event_type, payload, transition_id)


__all__ = [
    "ProviderResolver",
    "TeamPlanningMiddleware",
]
