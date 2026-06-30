from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from uuid import UUID

from pydantic import BaseModel, Field, ValidationError

from awesome_agent.domain.enums import AgentKind, DispatchStatus, EventType, RunMode
from awesome_agent.domain.models import Agent, Run
from awesome_agent.modeling import (
    ModelMessage,
    ModelProvider,
    ModelRequest,
    ModelTurn,
    SystemMessage,
    ToolCall,
    ToolChoice,
    ToolChoiceMode,
    ToolDefinition,
    ToolResultMessage,
    UserMessage,
)
from awesome_agent.persistence.team import TeamRepository
from awesome_agent.runtime.agent_loop import TeamAgentLoop
from awesome_agent.runtime.capabilities import (
    READ_ONLY_TEAM_TOOLS,
    WRITE_TEAM_TOOLS,
    CapabilityPurpose,
    CapabilityResolver,
    EffectiveToolPolicy,
)
from awesome_agent.runtime.dispatch import ChildRunWait, PermanentExecutionError
from awesome_agent.runtime.graphs import TEAM_ROLE_ROUTE
from awesome_agent.runtime.repository import RuntimeRepository
from awesome_agent.runtime.team_assignments import (
    TeamAssignment,
    TeamAssignmentKind,
    TeamAssignmentStatus,
    TeamChildResult,
    validate_assignment_graph,
)
from awesome_agent.runtime.team_budget import build_team_attribution
from awesome_agent.runtime.team_mailbox import (
    MailboxMessage,
    MailboxMessageStatus,
    MailboxMessageType,
)
from awesome_agent.runtime.team_mailbox_policy import (
    TEAM_MAILBOX_LIST_TOOL,
    TEAM_MAILBOX_SEND_TOOL,
    TEAM_MAILBOX_TOOLS,
    MailboxPolicyError,
    resolve_teammate_mailbox_route,
)
from awesome_agent.runtime.team_role_artifacts import (
    role_changed_files,
    role_git_diff,
)
from awesome_agent.tools.repository import (
    build_modifying_executor,
    build_modifying_registry,
    execute_repository_call,
    model_tool_definitions,
)

ProviderResolver = Callable[[str], ModelProvider]
RoleEventSink = Callable[[EventType, dict[str, object], str], Awaitable[None]]

_WRITE_TOOLS = WRITE_TEAM_TOOLS
_TEAM_CREATE_SUBAGENT = "team.create_subagent"
_READ_ONLY_TEAM_TOOLS = READ_ONLY_TEAM_TOOLS


class TeamCreateSubagentArguments(BaseModel):
    goal: str = Field(min_length=1, max_length=4000)
    allowed_tools: list[str] = Field(min_length=1, max_length=12)
    allowed_skills: list[str] = Field(default_factory=list, max_length=20)
    acceptance_criteria: list[str] = Field(min_length=1, max_length=8)


class TeamMailboxListArguments(BaseModel):
    include_sent: bool = False
    unread_only: bool = False
    limit: int = Field(default=10, ge=1, le=20)


class TeamMailboxSendArguments(BaseModel):
    recipient_run_id: UUID
    message_type: MailboxMessageType
    subject: str = Field(min_length=1, max_length=160)
    body_summary: str = Field(min_length=1, max_length=2000)
    requires_response: bool = False
    response_to_message_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class RoleLoopPolicy:
    allowed_tools: list[str]
    allowed_skills: list[str]
    can_write: bool
    acceptance_criteria: list[str]
    effective_tools: EffectiveToolPolicy | None = None
    max_model_turns: int = 20
    max_tool_calls: int = 60

    @property
    def tool_names(self) -> tuple[str, ...]:
        if self.effective_tools is None:
            return tuple(self.allowed_tools)
        return self.effective_tools.tool_names

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
        team_loop: TeamAgentLoop | None = None,
        max_model_turns: int = 20,
        max_tool_calls: int = 60,
    ) -> None:
        self.provider_resolver = provider_resolver
        self.team_loop = team_loop or TeamAgentLoop()
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
        repository: RuntimeRepository,
        team_repository: TeamRepository,
        subagent_results: list[TeamChildResult] | None = None,
        validation_feedback: str | None = None,
        event_sink: RoleEventSink | None = None,
    ) -> RoleLoopResult:
        registry = build_modifying_registry()
        executor = build_modifying_executor(registry)
        tool_definitions = _filter_tool_definitions(
            model_tool_definitions(registry),
            policy.tool_names,
        )
        if _TEAM_CREATE_SUBAGENT in policy.tool_names:
            tool_definitions.append(_create_subagent_tool_definition())
        if assignment.kind is TeamAssignmentKind.TEAMMATE:
            if TEAM_MAILBOX_LIST_TOOL in policy.tool_names:
                tool_definitions.append(_mailbox_list_tool_definition())
            if TEAM_MAILBOX_SEND_TOOL in policy.tool_names:
                tool_definitions.append(_mailbox_send_tool_definition())
        provider = self.provider_resolver(agent.model)
        mailbox_directory = await _mailbox_directory(
            assignment=assignment,
            policy=policy,
            team_repository=team_repository,
        )
        messages = _initial_messages(
            run,
            assignment,
            policy,
            subagent_results=subagent_results or [],
            validation_feedback=validation_feedback,
            mailbox_directory=mailbox_directory,
        )
        model_turn_count = 0
        tool_call_count = 0
        successful_inspections = len(subagent_results or [])
        successful_writes = 0
        diff_after_last_write = False
        final_answer = ""
        force_final = False

        while model_turn_count < min(policy.max_model_turns, self.max_model_turns):
            model_turn_count += 1
            started = monotonic()
            model_messages = list(messages)

            async def complete_role_turn(
                _: object,
                *,
                current_messages: list[ModelMessage] = model_messages,
            ) -> ModelTurn:
                return await provider.complete(
                    ModelRequest(
                        messages=current_messages,
                        tools=[] if force_final else tool_definitions,
                        tool_choice=ToolChoice(
                            mode=(
                                ToolChoiceMode.NONE
                                if force_final
                                else ToolChoiceMode.AUTO
                            )
                        ),
                    )
                )

            turn = await self.team_loop.wrap_model_call(
                object(),
                run=run,
                agent=agent,
                messages=model_messages,
                assignment_id=assignment.id,
                team_role=assignment.kind.value,
                agent_kind=agent.kind.value,
                metadata={"team_operation": "role_model", "turn": model_turn_count},
                handler=complete_role_turn,
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
                    tool_messages = list(messages)

                    async def execute_role_tool(
                        _: object,
                        *,
                        current_call: ToolCall = call,
                    ) -> ToolResultMessage:
                        return await _execute_call(
                            run=run,
                            agent=agent,
                            assignment=assignment,
                            policy=policy,
                            workspace=workspace,
                            repository=repository,
                            team_repository=team_repository,
                            call=current_call,
                            executor=executor,
                            event_sink=event_sink,
                        )

                    result = await self.team_loop.wrap_tool_call(
                        object(),
                        run=run,
                        agent=agent,
                        messages=tool_messages,
                        assignment_id=assignment.id,
                        team_role=assignment.kind.value,
                        agent_kind=agent.kind.value,
                        metadata={
                            "team_operation": "role_tool",
                            "tool": call.name,
                            "call_id": call.call_id,
                        },
                        handler=execute_role_tool,
                    )
                    tool_call_count += 1
                    if not result.is_error:
                        if call.name in _READ_ONLY_TEAM_TOOLS:
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

        patch = await role_git_diff(workspace)
        changed_files = await role_changed_files(workspace)
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
    repository: RuntimeRepository,
    team_repository: TeamRepository,
    call: ToolCall,
    executor: object,
    event_sink: RoleEventSink | None,
) -> ToolResultMessage:
    started = monotonic()
    if call.name == _TEAM_CREATE_SUBAGENT:
        await _create_dynamic_subagent(
            run=run,
            agent=agent,
            assignment=assignment,
            policy=policy,
            repository=repository,
            team_repository=team_repository,
            call=call,
            event_sink=event_sink,
        )
        raise ChildRunWait("waiting_subagents")
    allowed = _policy_allows(policy, call.name)
    if not allowed and policy.can_write and call.name in _WRITE_TOOLS:
        raise PermanentExecutionError(f"team_role_tool_not_allowed: {call.name}")
    if not allowed:
        result = ToolResultMessage(
            call_id=call.call_id,
            content=f"Tool {call.name} is not allowed for this assignment.",
            is_error=True,
        )
    elif call.name == TEAM_MAILBOX_LIST_TOOL:
        result = await _execute_mailbox_list(
            run=run,
            agent=agent,
            assignment=assignment,
            team_repository=team_repository,
            call=call,
            event_sink=event_sink,
        )
    elif call.name == TEAM_MAILBOX_SEND_TOOL:
        result = await _execute_mailbox_send(
            run=run,
            agent=agent,
            assignment=assignment,
            repository=repository,
            team_repository=team_repository,
            call=call,
            event_sink=event_sink,
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
        capabilities = (
            set(policy.effective_tools.capabilities_for(call.name))
            if policy.effective_tools is not None
            else _legacy_capabilities(policy, call.name)
        )
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


async def _execute_mailbox_list(
    *,
    run: Run,
    agent: Agent,
    assignment: TeamAssignment,
    team_repository: TeamRepository,
    call: ToolCall,
    event_sink: RoleEventSink | None,
) -> ToolResultMessage:
    if assignment.kind is not TeamAssignmentKind.TEAMMATE:
        return ToolResultMessage(
            call_id=call.call_id,
            content="Only Teammates can list team mailbox messages.",
            is_error=True,
        )
    try:
        arguments = TeamMailboxListArguments.model_validate_json(call.arguments_json)
    except ValidationError as error:
        return ToolResultMessage(
            call_id=call.call_id,
            content=f"invalid team.mailbox_list arguments: {error}",
            is_error=True,
        )

    messages = await team_repository.list_mailbox_messages(run.id)
    filtered = [
        message
        for message in messages
        if arguments.include_sent or message.recipient_run_id == run.id
    ]
    if arguments.unread_only:
        filtered = [
            message
            for message in filtered
            if message.status is MailboxMessageStatus.UNREAD
        ]
    selected = filtered[: arguments.limit]
    read_ids: list[str] = []
    for message in selected:
        if (
            message.recipient_run_id == run.id
            and message.status is MailboxMessageStatus.UNREAD
        ):
            read = await team_repository.mark_mailbox_read(message.id)
            read_ids.append(str(read.id))
            await _emit(
                event_sink,
                run,
                assignment,
                agent,
                EventType.TEAM_MAILBOX_MESSAGE_READ,
                {"message_id": str(read.id), "route": read.route.value},
                f"team-mailbox-read:{read.id}",
            )

    return ToolResultMessage(
        call_id=call.call_id,
        content=json.dumps(
            {
                "messages": [_mailbox_message_payload(message) for message in selected],
                "marked_read_ids": read_ids,
            }
        ),
    )


async def _execute_mailbox_send(
    *,
    run: Run,
    agent: Agent,
    assignment: TeamAssignment,
    repository: RuntimeRepository,
    team_repository: TeamRepository,
    call: ToolCall,
    event_sink: RoleEventSink | None,
) -> ToolResultMessage:
    if assignment.kind is not TeamAssignmentKind.TEAMMATE:
        return ToolResultMessage(
            call_id=call.call_id,
            content="Only Teammates can send team mailbox messages.",
            is_error=True,
        )
    try:
        arguments = TeamMailboxSendArguments.model_validate_json(call.arguments_json)
    except ValidationError as error:
        return ToolResultMessage(
            call_id=call.call_id,
            content=f"invalid team.mailbox_send arguments: {error}",
            is_error=True,
        )

    try:
        recipient_run = await repository.get_run(arguments.recipient_run_id)
        route = resolve_teammate_mailbox_route(
            sender_run=run,
            sender_assignment=assignment,
            recipient_run=recipient_run,
            message_type=arguments.message_type,
        )
    except (KeyError, MailboxPolicyError) as error:
        return ToolResultMessage(
            call_id=call.call_id,
            content=str(error),
            is_error=True,
        )

    message = MailboxMessage(
        team_root_run_id=assignment.root_run_id,
        sender_run_id=run.id,
        sender_agent_id=agent.id,
        recipient_run_id=recipient_run.id,
        route=route,
        message_type=arguments.message_type,
        subject=arguments.subject,
        body_summary=arguments.body_summary,
        requires_response=arguments.requires_response,
        response_to_message_id=arguments.response_to_message_id,
    )

    if arguments.response_to_message_id is None:
        saved = await team_repository.create_mailbox_message(message)
    else:
        try:
            original = await team_repository.get_mailbox_message(
                arguments.response_to_message_id
            )
        except KeyError:
            return ToolResultMessage(
                call_id=call.call_id,
                content=(
                    f"Response message not found: {arguments.response_to_message_id}"
                ),
                is_error=True,
            )
        if original.recipient_run_id != run.id:
            return ToolResultMessage(
                call_id=call.call_id,
                content="Only the original recipient can respond to a mailbox message.",
                is_error=True,
            )
        if original.sender_run_id != recipient_run.id:
            return ToolResultMessage(
                call_id=call.call_id,
                content="Response recipient must be the original sender.",
                is_error=True,
            )
        _, saved = await team_repository.respond_to_mailbox_message(
            arguments.response_to_message_id,
            message,
        )
        await _emit(
            event_sink,
            run,
            assignment,
            agent,
            EventType.TEAM_MAILBOX_MESSAGE_RESPONDED,
            {
                "message_id": str(arguments.response_to_message_id),
                "response_message_id": str(saved.id),
                "route": saved.route.value,
            },
            f"team-mailbox-responded:{arguments.response_to_message_id}:{saved.id}",
        )

    await _emit(
        event_sink,
        run,
        assignment,
        agent,
        EventType.TEAM_MAILBOX_MESSAGE_CREATED,
        {
            "message_id": str(saved.id),
            "route": saved.route.value,
            "message_type": saved.message_type.value,
            "recipient_run_id": str(saved.recipient_run_id),
            "requires_response": saved.requires_response,
        },
        f"team-mailbox-created:{saved.id}",
    )
    return ToolResultMessage(
        call_id=call.call_id,
        content=json.dumps(
            {
                "message_id": str(saved.id),
                "route": saved.route.value,
                "status": saved.status.value,
            }
        ),
    )


def _mailbox_message_payload(message: MailboxMessage) -> dict[str, object]:
    return {
        "id": str(message.id),
        "route": message.route.value,
        "message_type": message.message_type.value,
        "status": message.status.value,
        "sender_run_id": str(message.sender_run_id),
        "recipient_run_id": str(message.recipient_run_id),
        "subject": message.subject,
        "body_summary": message.body_summary,
        "requires_response": message.requires_response,
        "response_to_message_id": (
            str(message.response_to_message_id)
            if message.response_to_message_id is not None
            else None
        ),
    }


def _initial_messages(
    run: Run,
    assignment: TeamAssignment,
    policy: RoleLoopPolicy,
    *,
    subagent_results: list[TeamChildResult],
    validation_feedback: str | None,
    mailbox_directory: str | None,
) -> list[ModelMessage]:
    criteria = "\n".join(f"- {item}" for item in policy.acceptance_criteria)
    messages: list[ModelMessage] = [
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
    if subagent_results:
        summaries = "\n".join(
            f"- {result.status}: {result.summary[:1000]}" for result in subagent_results
        )
        messages.append(
            UserMessage(
                content=(
                    "Completed Subagent results available to this Teammate:\n"
                    f"{summaries}"
                )
            )
        )
    if validation_feedback:
        messages.append(
            SystemMessage(
                content=(
                    "Validation failed. Rework the implementation using this "
                    "bounded evidence, then call repo.diff again before "
                    f"finishing:\n{validation_feedback}"
                )
            )
        )
    if mailbox_directory:
        messages.append(UserMessage(content=mailbox_directory))
    return messages


async def _mailbox_directory(
    *,
    assignment: TeamAssignment,
    policy: RoleLoopPolicy,
    team_repository: TeamRepository,
) -> str | None:
    if assignment.kind is not TeamAssignmentKind.TEAMMATE:
        return None
    if not TEAM_MAILBOX_TOOLS.intersection(policy.allowed_tools):
        return None
    lines = [
        "Mailbox directory for assignment-granted coordination:",
        f"- leader root: run_id={assignment.root_run_id}",
    ]
    assignments = await team_repository.list_assignments(
        assignment.root_run_id,
        include_inactive=True,
    )
    for item in assignments:
        if (
            item.kind is TeamAssignmentKind.TEAMMATE
            and item.child_run_id != assignment.child_run_id
            and item.status is TeamAssignmentStatus.ACTIVE
        ):
            lines.append(f"- teammate {item.role_profile}: run_id={item.child_run_id}")
    lines.append(
        "Use team.mailbox_send only for bounded question/status coordination; "
        "mailbox messages do not grant tools, mutate assignments, or bypass Verifier."
    )
    return "\n".join(lines)


async def _create_dynamic_subagent(
    *,
    run: Run,
    agent: Agent,
    assignment: TeamAssignment,
    policy: RoleLoopPolicy,
    repository: RuntimeRepository,
    team_repository: TeamRepository,
    call: ToolCall,
    event_sink: RoleEventSink | None,
) -> None:
    if (
        assignment.kind is not TeamAssignmentKind.TEAMMATE
        or run.depth != 1
        or not assignment.can_delegate
    ):
        raise PermanentExecutionError("only teammates can create subagents")
    arguments = _parse_create_subagent(call)
    _validate_subagent_arguments(arguments, policy, assignment=assignment)
    existing = await _find_subagent_for_call(
        team_repository,
        assignment=assignment,
        call_id=call.call_id,
    )
    if existing is not None:
        if existing.status is TeamAssignmentStatus.ACTIVE:
            raise ChildRunWait("waiting_subagents")
        return
    active = [
        item
        for item in await team_repository.list_assignments(
            assignment.root_run_id,
            include_inactive=True,
        )
        if item.parent_run_id == run.id
        and item.kind is TeamAssignmentKind.SUBAGENT
        and item.status is TeamAssignmentStatus.ACTIVE
    ]
    if len(active) >= min(3, assignment.max_subagents):
        raise PermanentExecutionError("active subagent limit reached")
    child = Run(
        goal=arguments.goal,
        mode=RunMode.TEAM,
        repository_id=run.repository_id,
        base_commit=run.base_commit,
        intent=run.intent,
        execution_kind=run.execution_kind,
        parent_run_id=run.id,
        root_run_id=run.root_run_id or assignment.root_run_id,
        depth=2,
        child_role=TeamAssignmentKind.SUBAGENT.value,
        runtime_route=TEAM_ROLE_ROUTE,
        dispatch_status=DispatchStatus.QUEUED,
        workspace_path=run.workspace_path,
        integration_branch=run.integration_branch,
        workspace_state=run.workspace_state,
        graph_thread_id=f"run:{run.id}:subagent:{call.call_id}",
    )
    subagent = Agent(
        run_id=child.id,
        parent_agent_id=agent.id,
        kind=AgentKind.SUBAGENT,
        profile="subagent",
        model=agent.model,
    )
    child_assignment = TeamAssignment(
        root_run_id=assignment.root_run_id,
        parent_run_id=run.id,
        child_run_id=child.id,
        kind=TeamAssignmentKind.SUBAGENT,
        role_profile="subagent",
        runtime_route=TEAM_ROLE_ROUTE,
        goal=arguments.goal,
        allowed_tools=arguments.allowed_tools,
        allowed_skills=arguments.allowed_skills,
        can_write=False,
        can_delegate=False,
        max_subagents=0,
        acceptance_criteria=arguments.acceptance_criteria,
        handoff_context={"created_by_tool_call_id": call.call_id},
    )
    validate_assignment_graph(child_assignment)
    await repository.create_run(child, subagent)
    await team_repository.create_assignment(child_assignment)
    await _emit(
        event_sink,
        run,
        assignment,
        agent,
        EventType.TEAM_SUBAGENT_REQUESTED,
        {
            "tool_call_id": call.call_id,
            "child_run_id": str(child.id),
            "assignment_id": str(child_assignment.id),
            "goal": arguments.goal,
        },
        f"team-subagent-requested:{call.call_id}",
    )
    await _emit(
        event_sink,
        child,
        child_assignment,
        subagent,
        EventType.TEAM_CHILD_RUN_CREATED,
        {
            "child_run_id": str(child.id),
            "assignment_id": str(child_assignment.id),
            "kind": child_assignment.kind.value,
        },
        f"team-child-created:{child.id}",
    )
    await _emit(
        event_sink,
        child,
        child_assignment,
        subagent,
        EventType.TEAM_ASSIGNMENT_CREATED,
        {
            "assignment_id": str(child_assignment.id),
            "child_run_id": str(child.id),
            "kind": child_assignment.kind.value,
        },
        f"team-assignment-created:{child_assignment.id}",
    )


def _parse_create_subagent(call: ToolCall) -> TeamCreateSubagentArguments:
    try:
        raw = json.loads(call.arguments_json)
        return TeamCreateSubagentArguments.model_validate(raw)
    except (json.JSONDecodeError, ValidationError) as error:
        message = f"invalid team.create_subagent arguments: {error}"
        raise PermanentExecutionError(message) from error


def _validate_subagent_arguments(
    arguments: TeamCreateSubagentArguments,
    policy: RoleLoopPolicy,
    *,
    assignment: TeamAssignment,
) -> None:
    requested_tools = set(arguments.allowed_tools)
    if policy.effective_tools is None:
        allowed = set(policy.allowed_tools)
        if not requested_tools.issubset(allowed):
            raise PermanentExecutionError(
                "subagent tools must be a subset of teammate tools"
            )
        if any(tool not in _READ_ONLY_TEAM_TOOLS for tool in requested_tools):
            raise PermanentExecutionError("subagent tools must be read-only")
    else:
        granted = set(policy.effective_tools.tool_names)
        if not requested_tools.issubset(granted):
            raise PermanentExecutionError(
                "subagent tools must be a subset of teammate tools"
            )
        subagent_policy = CapabilityResolver().resolve_team_assignment(
            assignment,
            purpose=CapabilityPurpose.SUBAGENT_GRANT,
            requested_tools=arguments.allowed_tools,
        )
        denied_requested = [
            decision
            for decision in subagent_policy.denied
            if decision.tool_name in requested_tools
        ]
        if denied_requested:
            raise PermanentExecutionError("subagent tools must be read-only")
    if not set(arguments.allowed_skills).issubset(policy.allowed_skills):
        raise PermanentExecutionError(
            "subagent skills must be a subset of teammate skills"
        )


async def _find_subagent_for_call(
    team_repository: TeamRepository,
    *,
    assignment: TeamAssignment,
    call_id: str,
) -> TeamAssignment | None:
    for item in await team_repository.list_assignments(
        assignment.root_run_id,
        include_inactive=True,
    ):
        if (
            item.parent_run_id == assignment.child_run_id
            and item.kind is TeamAssignmentKind.SUBAGENT
            and item.handoff_context.get("created_by_tool_call_id") == call_id
        ):
            return item
    return None


def _create_subagent_tool_definition() -> ToolDefinition:
    return ToolDefinition(
        name=_TEAM_CREATE_SUBAGENT,
        description=(
            "Create one read-only Subagent child Run for bounded delegated "
            "repository evidence. Only Teammates may call this tool."
        ),
        input_schema=TeamCreateSubagentArguments.model_json_schema(),
    )


def _mailbox_list_tool_definition() -> ToolDefinition:
    return ToolDefinition(
        name=TEAM_MAILBOX_LIST_TOOL,
        description=(
            "List assignment-scoped team mailbox messages for this Teammate. "
            "Unread received messages may be marked read."
        ),
        input_schema=TeamMailboxListArguments.model_json_schema(),
    )


def _mailbox_send_tool_definition() -> ToolDefinition:
    return ToolDefinition(
        name=TEAM_MAILBOX_SEND_TOOL,
        description=(
            "Send a bounded question or status mailbox message to the Leader "
            "root Run or a sibling Teammate Run."
        ),
        input_schema=TeamMailboxSendArguments.model_json_schema(),
    )


def _filter_tool_definitions(
    definitions: list[ToolDefinition],
    allowed_tools: list[str] | tuple[str, ...],
) -> list[ToolDefinition]:
    allowed = set(allowed_tools)
    return [definition for definition in definitions if definition.name in allowed]


def _policy_allows(policy: RoleLoopPolicy, tool_name: str) -> bool:
    if policy.effective_tools is None:
        return tool_name in policy.allowed_tools
    return policy.effective_tools.allows(tool_name)


def _legacy_capabilities(policy: RoleLoopPolicy, tool_name: str) -> set[str]:
    if tool_name == "repo.apply_patch" and policy.can_write:
        return {"repository:write"}
    if tool_name == "shell.execute" and policy.can_write:
        return {"shell:execute"}
    return {"repository:read"}


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
