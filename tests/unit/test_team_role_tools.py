from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from awesome_agent.domain.enums import AgentKind, RunMode
from awesome_agent.domain.models import Agent, Run
from awesome_agent.modeling import ToolCall, ToolResultMessage
from awesome_agent.persistence.tool_invocations import InMemoryToolInvocationRepository
from awesome_agent.runtime.team_role_tools import (
    complete_team_role_tool_invocation,
    start_team_role_tool_invocation,
    team_role_tool_idempotency_key,
)


@pytest.mark.asyncio
async def test_team_role_tool_invocation_uses_run_scoped_idempotency(
    tmp_path: Path,
) -> None:
    repository = InMemoryToolInvocationRepository()
    run = Run(goal="role", mode=RunMode.TEAM)
    agent = Agent(
        run_id=run.id,
        kind=AgentKind.TEAMMATE,
        profile="backend",
        model="m",
    )
    call = ToolCall(
        call_id="read-1",
        name="repo.read",
        arguments_json='{"path":"README.md"}',
    )

    invocation = await start_team_role_tool_invocation(
        repository=repository,
        run=run,
        agent=agent,
        call=call,
        workspace=tmp_path,
    )
    assert invocation is not None
    assert invocation.idempotency_key == team_role_tool_idempotency_key(
        run=run,
        agent=agent,
        call=call,
    )
    assert invocation.status == "started"

    await complete_team_role_tool_invocation(
        repository=repository,
        invocation=invocation,
        result=ToolResultMessage(call_id=call.call_id, content="README fixture"),
    )

    stored = await repository.get(invocation.id)
    assert stored.status == "completed"
    assert stored.result_content == "README fixture"


@pytest.mark.asyncio
async def test_team_role_tool_invocation_reuses_existing_idempotency(
    tmp_path: Path,
) -> None:
    repository = InMemoryToolInvocationRepository()
    run = Run(goal="role", mode=RunMode.TEAM)
    agent = Agent(
        run_id=run.id,
        kind=AgentKind.TEAMMATE,
        profile="backend",
        model="m",
    )
    call = ToolCall(
        call_id="read-1",
        name="repo.read",
        arguments_json='{"path":"README.md"}',
    )

    first = await start_team_role_tool_invocation(
        repository=repository,
        run=run,
        agent=agent,
        call=call,
        workspace=tmp_path,
    )
    second = await start_team_role_tool_invocation(
        repository=repository,
        run=run,
        agent=agent,
        call=call,
        workspace=tmp_path,
    )

    assert first == second
    assert len(await repository.list_for_run(run.id)) == 1


def test_team_role_tool_idempotency_key_includes_agent_id() -> None:
    run = Run(goal="role", mode=RunMode.TEAM)
    call = ToolCall(call_id="read-1", name="repo.read", arguments_json="{}")
    first = Agent(
        id=uuid4(),
        run_id=run.id,
        kind=AgentKind.TEAMMATE,
        profile="backend",
        model="m",
    )
    second = first.model_copy(update={"id": uuid4()})

    assert team_role_tool_idempotency_key(run=run, agent=first, call=call) != (
        team_role_tool_idempotency_key(run=run, agent=second, call=call)
    )
