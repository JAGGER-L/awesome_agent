from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from mem0 import AsyncMemoryClient

from awesome_agent.memory.external import Mem0PlatformMemory
from awesome_agent.memory.models import MemoryCandidate, MemoryKind, MemorySource


def _candidate() -> MemoryCandidate:
    return MemoryCandidate(
        kind=MemoryKind.OPERATIONAL,
        content="Run targeted tests after each logical change.",
        source=MemorySource.AGENT_EXPERIENCE,
    )


@pytest.mark.asyncio
async def test_mem0_add_and_search() -> None:
    client = cast(AsyncMemoryClient, cast(Any, AsyncMock()))
    client.add.return_value = {"results": []}
    client.search.return_value = {
        "results": [
            {
                "id": "memory-1",
                "memory": "Use targeted tests.",
                "metadata": {"kind": "operational"},
            }
        ]
    }
    memory = Mem0PlatformMemory(api_key="test", client=client)

    assert await memory.add(_candidate(), user_id="user", project_id="project")
    results = await memory.search(
        "testing",
        user_id="user",
        project_id="project",
    )

    assert results[0].content == "Use targeted tests."


@pytest.mark.asyncio
async def test_mem0_failure_degrades_gracefully() -> None:
    client = cast(AsyncMemoryClient, cast(Any, AsyncMock()))
    client.add.side_effect = RuntimeError("unavailable")
    client.search.side_effect = RuntimeError("unavailable")
    memory = Mem0PlatformMemory(api_key="test", client=client)

    assert not await memory.add(_candidate(), user_id="user", project_id="project")
    assert await memory.search("query", user_id="user", project_id="project") == []
