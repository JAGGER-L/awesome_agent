from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from awesome_agent.domain.enums import RiskLevel
from awesome_agent.domain.models import Agent, Run
from awesome_agent.modeling import ToolCall, ToolResultMessage
from awesome_agent.persistence.tool_invocations import (
    DurableToolInvocation,
    ToolInvocationRepository,
)
from awesome_agent.tools.repository import (
    canonical_arguments_hash_from_arguments,
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
    arguments = parse_team_role_tool_arguments(call)
    arguments_hash = canonical_arguments_hash_from_arguments(arguments)
    idempotency_key = team_role_tool_idempotency_key(
        run=run,
        agent=agent,
        call=call,
    )
    existing = await repository.get_by_idempotency_key(run.id, idempotency_key)
    if existing is not None:
        return existing
    path_refs, preimage_hashes = team_role_tool_effect_metadata(
        tool_name=call.name,
        arguments=arguments,
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


def parse_team_role_tool_arguments(call: ToolCall) -> dict[str, Any]:
    try:
        raw = json.loads(call.arguments_json)
    except json.JSONDecodeError as error:
        raise ValueError(f"Arguments are not valid JSON: {error.msg}") from error
    if not isinstance(raw, dict):
        raise ValueError("Tool arguments must be a JSON object.")
    return dict(raw)


def team_role_tool_effect_metadata(
    *,
    tool_name: str,
    arguments: dict[str, Any],
    workspace: Path,
) -> tuple[list[str], dict[str, str]]:
    if tool_name != "repo.apply_patch":
        return [], {}
    return repository_tool_effect_metadata(
        tool_name,
        arguments,
        workspace=workspace,
    )


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
