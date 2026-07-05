from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from awesome_agent.domain.enums import RiskLevel
from awesome_agent.domain.models import Agent, Run
from awesome_agent.modeling import ToolCall, ToolResultMessage
from awesome_agent.persistence.tool_invocations import (
    DurableToolInvocation,
    ToolInvocationRepository,
)
from awesome_agent.tools.repository import (
    canonical_arguments_hash,
    parse_tool_call_arguments,
    repository_tool_effect_metadata,
    tool_invocation_uuid,
)


def team_role_tool_idempotency_key(
    *,
    run: Run,
    agent: Agent,
    call: ToolCall,
) -> str:
    return f"team-role:{run.id}:{agent.id}:{call.call_id}"


async def start_team_role_tool_invocation(
    *,
    repository: ToolInvocationRepository | None,
    run: Run,
    agent: Agent,
    call: ToolCall,
    workspace: Path,
    tool_version: str = "1",
    risk_level: RiskLevel = RiskLevel.LOW,
) -> DurableToolInvocation | None:
    if repository is None:
        return None
    arguments = parse_tool_call_arguments(call)
    arguments_hash = canonical_arguments_hash(call)
    idempotency_key = team_role_tool_idempotency_key(
        run=run,
        agent=agent,
        call=call,
    )
    existing = await repository.get_by_idempotency_key(run.id, idempotency_key)
    if existing is not None:
        return existing
    path_refs, preimage_hashes = repository_tool_effect_metadata(
        call.name,
        arguments,
        workspace=workspace,
    )
    now = datetime.now(UTC)
    invocation = DurableToolInvocation(
        id=tool_invocation_uuid(idempotency_key),
        run_id=run.id,
        agent_id=agent.id,
        tool_name=call.name,
        tool_version=tool_version,
        status="started",
        idempotency_key=idempotency_key,
        arguments_hash=arguments_hash,
        risk_level=risk_level.value,
        path_refs=path_refs,
        preimage_hashes=preimage_hashes,
        started_at=now,
        updated_at=now,
    )
    return await repository.upsert(invocation)


async def complete_team_role_tool_invocation(
    *,
    repository: ToolInvocationRepository | None,
    invocation: DurableToolInvocation | None,
    result: ToolResultMessage,
) -> None:
    if repository is None or invocation is None:
        return
    now = datetime.now(UTC)
    await repository.upsert(
        replace(
            invocation,
            status="failed" if result.is_error else "completed",
            result_summary=result.content[:500],
            result_content=result.content,
            result_is_error=result.is_error,
            error=result.content[:500] if result.is_error else None,
            completed_at=now,
            updated_at=now,
        )
    )


async def fail_team_role_tool_invocation(
    *,
    repository: ToolInvocationRepository | None,
    invocation: DurableToolInvocation | None,
    status: str,
    error: str,
) -> None:
    if repository is None or invocation is None:
        return
    now = datetime.now(UTC)
    await repository.upsert(
        replace(
            invocation,
            status=status,
            error=error[:500],
            completed_at=now if status != "approval_pending" else None,
            updated_at=now,
        )
    )
