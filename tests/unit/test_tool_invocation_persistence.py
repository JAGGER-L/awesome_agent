from uuid import uuid4

import pytest

from awesome_agent.persistence.tool_invocations import (
    DurableToolInvocation,
    InMemoryToolInvocationRepository,
)


@pytest.mark.asyncio
async def test_tool_invocation_persistence_redacts_result_fields() -> None:
    tool_repository = InMemoryToolInvocationRepository()
    invocation = DurableToolInvocation(
        id=uuid4(),
        run_id=uuid4(),
        agent_id=uuid4(),
        tool_name="shell.execute",
        tool_version="1",
        status="completed",
        idempotency_key="tool-redaction",
        arguments_hash="args",
        risk_level="medium",
        result_summary="TOKEN=abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG",
        result_content="PASSWORD=hunter2",
        result_is_error=False,
        error="OPENAI_API_KEY=sk-proj-abcdefghijklmnopqrstuvwxyz1234567890",
    )

    await tool_repository.upsert(invocation)
    stored = await tool_repository.get(invocation.id)

    assert stored is not None
    assert "abcdefghijklmnopqrstuvwxyz" not in str(stored.result_summary)
    assert stored.result_content == "PASSWORD=[REDACTED:password]"
    assert "sk-proj-" not in str(stored.error)
