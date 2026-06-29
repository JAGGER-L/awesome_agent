from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from time import monotonic

from awesome_agent.domain.enums import EventType
from awesome_agent.domain.models import Agent, Run
from awesome_agent.modeling import (
    ModelMessage,
    ModelProvider,
    ModelRequest,
    SystemMessage,
    ToolCall,
    ToolChoice,
    ToolChoiceMode,
    ToolDefinition,
    ToolResultMessage,
    UserMessage,
)
from awesome_agent.runtime.dispatch import PermanentExecutionError
from awesome_agent.runtime.team_assignments import TeamAssignment
from awesome_agent.runtime.team_budget import build_team_attribution
from awesome_agent.sandbox.process import run_process
from awesome_agent.tools.repository import (
    build_modifying_executor,
    build_modifying_registry,
    execute_repository_call,
    model_tool_definitions,
)

ProviderResolver = Callable[[str], ModelProvider]
RoleEventSink = Callable[[EventType, dict[str, object], str], Awaitable[None]]

_WRITE_TOOLS = {"repo.apply_patch", "shell.execute"}


@dataclass(frozen=True, slots=True)
class RoleLoopPolicy:
    allowed_tools: list[str]
    allowed_skills: list[str]
    can_write: bool
    acceptance_criteria: list[str]
    max_model_turns: int = 20
    max_tool_calls: int = 60

    @property
    def requires_read_evidence(self) -> bool:
        return not self.can_write


@dataclass(frozen=True, slots=True)
class RoleLoopResult:
    summary: str
    final_answer: str
    model_turn_count: int
    tool_call_count: int
    successful_inspections: int
    changed_files: list[str]
    patch: str
    no_change: bool


class RoleLoop:
    def __init__(
        self,
        *,
        provider_resolver: ProviderResolver,
        max_model_turns: int = 20,
        max_tool_calls: int = 60,
    ) -> None:
        self.provider_resolver = provider_resolver
        self.max_model_turns = max_model_turns
        self.max_tool_calls = max_tool_calls

    async def execute(
        self,
        *,
        run: Run,
        agent: Agent,
        assignment: TeamAssignment,
        policy: RoleLoopPolicy,
        workspace: Path,
        event_sink: RoleEventSink | None = None,
    ) -> RoleLoopResult:
        registry = build_modifying_registry()
        executor = build_modifying_executor(registry)
        tool_definitions = _filter_tool_definitions(
            model_tool_definitions(registry),
            policy.allowed_tools,
        )
        provider = self.provider_resolver(agent.model)
        messages = _initial_messages(run, assignment, policy)
        model_turn_count = 0
        tool_call_count = 0
        successful_inspections = 0
        successful_writes = 0
        diff_after_last_write = False
        final_answer = ""
        force_final = False

        while model_turn_count < min(policy.max_model_turns, self.max_model_turns):
            model_turn_count += 1
            started = monotonic()
            turn = await provider.complete(
                ModelRequest(
                    messages=messages,
                    tools=[] if force_final else tool_definitions,
                    tool_choice=ToolChoice(
                        mode=ToolChoiceMode.NONE if force_final else ToolChoiceMode.AUTO
                    ),
                )
            )
            await _emit(
                event_sink,
                run,
                assignment,
                agent,
                EventType.MODEL_CALL_CREATED,
                {
                    "turn": model_turn_count,
                    "status": "completed",
                    "provider": turn.provider,
                    "model": turn.model,
                    "stop_reason": turn.stop_reason.value,
                    "input_tokens": turn.usage.input_tokens,
                    "output_tokens": turn.usage.output_tokens,
                    "reasoning_tokens": turn.usage.reasoning_tokens,
                    "latency_ms": _elapsed_ms(started),
                },
                f"model:{agent.id}:{model_turn_count}",
            )
            if turn.assistant.tool_calls and not force_final:
                messages.append(turn.assistant)
                for call in turn.assistant.tool_calls:
                    limit = min(policy.max_tool_calls, self.max_tool_calls)
                    if tool_call_count >= limit:
                        raise PermanentExecutionError("team_role_tool_budget_exhausted")
                    result = await _execute_call(
                        run=run,
                        agent=agent,
                        assignment=assignment,
                        policy=policy,
                        workspace=workspace,
                        call=call,
                        executor=executor,
                        event_sink=event_sink,
                    )
                    tool_call_count += 1
                    if not result.is_error:
                        successful_inspections += 1
                        if call.name in _WRITE_TOOLS:
                            successful_writes += 1
                            diff_after_last_write = False
                        if call.name == "repo.diff" and successful_writes:
                            diff_after_last_write = True
                    messages.append(result)
                continue

            final_answer = turn.assistant.content.strip()
            if policy.requires_read_evidence and successful_inspections <= 0:
                messages.extend(
                    [
                        turn.assistant,
                        SystemMessage(
                            content=(
                                "Do not finish yet. Use an allowed repository read "
                                "tool and return evidence from the workspace."
                            )
                        ),
                    ]
                )
                continue
            if successful_writes and not diff_after_last_write:
                messages.extend(
                    [
                        turn.assistant,
                        SystemMessage(
                            content=(
                                "Do not finish yet. You changed files; call "
                                "repo.diff after the last write before finalizing."
                            )
                        ),
                    ]
                )
                continue
            break
        else:
            raise PermanentExecutionError("team_role_model_turn_budget_exhausted")

        patch = await _git_diff(workspace)
        changed_files = await _git_changed_files(workspace)
        return RoleLoopResult(
            summary=final_answer or "Team role completed.",
            final_answer=final_answer or "Team role completed.",
            model_turn_count=model_turn_count,
            tool_call_count=tool_call_count,
            successful_inspections=successful_inspections,
            changed_files=changed_files,
            patch=patch,
            no_change=not bool(patch.strip()),
        )


async def _execute_call(
    *,
    run: Run,
    agent: Agent,
    assignment: TeamAssignment,
    policy: RoleLoopPolicy,
    workspace: Path,
    call: ToolCall,
    executor: object,
    event_sink: RoleEventSink | None,
) -> ToolResultMessage:
    started = monotonic()
    allowed = call.name in policy.allowed_tools
    if not allowed and policy.can_write and call.name in _WRITE_TOOLS:
        raise PermanentExecutionError(f"team_role_tool_not_allowed: {call.name}")
    if not allowed:
        result = ToolResultMessage(
            call_id=call.call_id,
            content=f"Tool {call.name} is not allowed for this assignment.",
            is_error=True,
        )
    elif call.name in _WRITE_TOOLS and not policy.can_write:
        result = ToolResultMessage(
            call_id=call.call_id,
            content=(
                f"Tool {call.name} is write-capable and this assignment is read-only."
            ),
            is_error=True,
        )
    else:
        capabilities = {"repository:read"}
        if policy.can_write:
            capabilities.add("repository:write")
            capabilities.add("shell:execute")
        result = await execute_repository_call(
            executor,  # type: ignore[arg-type]
            call,
            workspace=workspace,
            agent_id=agent.id,
            profile=agent.profile,
            capabilities=capabilities,
        )
    await _emit(
        event_sink,
        run,
        assignment,
        agent,
        EventType.TOOL_CALL_CREATED,
        {
            "call_id": call.call_id,
            "tool": call.name,
            "status": "failed" if result.is_error else "completed",
            "result_summary": result.content[:500],
            "latency_ms": _elapsed_ms(started),
        },
        f"tool:{agent.id}:{call.call_id}",
    )
    return result


def _initial_messages(
    run: Run,
    assignment: TeamAssignment,
    policy: RoleLoopPolicy,
) -> list[ModelMessage]:
    criteria = "\n".join(f"- {item}" for item in policy.acceptance_criteria)
    return [
        SystemMessage(
            content=(
                "You are a bounded Teammate/Subagent inside a distributed coding "
                "team. Use only the provided tools. Report uncertainty. Writing "
                "assignments must call repo.diff after the last write before "
                "finishing."
            )
        ),
        UserMessage(
            content=(
                f"Root/child goal: {run.goal}\n"
                f"Assignment goal: {assignment.goal}\n"
                f"Allowed skills: {', '.join(policy.allowed_skills) or 'none'}\n"
                f"Can write: {policy.can_write}\n"
                f"Acceptance criteria:\n{criteria or '- Return bounded evidence.'}"
            )
        ),
    ]


def _filter_tool_definitions(
    definitions: list[ToolDefinition],
    allowed_tools: list[str],
) -> list[ToolDefinition]:
    allowed = set(allowed_tools)
    return [definition for definition in definitions if definition.name in allowed]


async def _git_diff(workspace: Path) -> str:
    result = await run_process(
        ["git", "diff", "--", "."],
        command_label="team role diff",
        workspace=workspace,
        timeout_seconds=30,
    )
    if result.exit_code != 0:
        raise PermanentExecutionError(
            result.stderr or result.stdout or "git diff failed"
        )
    return result.stdout


async def _git_changed_files(workspace: Path) -> list[str]:
    result = await run_process(
        ["git", "diff", "--name-only", "--", "."],
        command_label="team role changed files",
        workspace=workspace,
        timeout_seconds=30,
    )
    if result.exit_code != 0:
        raise PermanentExecutionError(
            result.stderr or result.stdout or "git diff --name-only failed"
        )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


async def _emit(
    event_sink: RoleEventSink | None,
    run: Run,
    assignment: TeamAssignment,
    agent: Agent,
    event_type: EventType,
    payload: dict[str, object],
    transition_id: str,
) -> None:
    if event_sink is None:
        return
    await event_sink(
        event_type,
        {
            **build_team_attribution(
                run=run,
                assignment=assignment,
                agent_id=agent.id,
            ),
            **payload,
        },
        transition_id,
    )


def _elapsed_ms(started: float) -> int:
    return max(0, int((monotonic() - started) * 1000))
