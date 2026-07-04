import pytest

from awesome_agent.memory.external import FailingMemoryProvider, NoopMemoryProvider
from awesome_agent.memory.models import MemoryAddRequest, MemoryTarget


@pytest.mark.asyncio
async def test_noop_provider_is_non_blocking() -> None:
    provider = NoopMemoryProvider()

    assert await provider.initialize("thread-1")
    assert await provider.retrieve("query", thread_id="thread-1", limit=5) == []
    assert await provider.add(
        MemoryAddRequest(
            target=MemoryTarget.USER,
            content="Prefer concise answers.",
            source="explicit_user_request",
        ),
        metadata={"thread_id": "thread-1"},
    )
    assert await provider.delete("mem_1")
    assert await provider.sync_turn(
        user_message="hi",
        assistant_message="hello",
        metadata={"thread_id": "thread-1"},
    )


@pytest.mark.asyncio
async def test_failing_provider_reports_failure_without_raising() -> None:
    provider = FailingMemoryProvider("unavailable")

    assert not await provider.initialize("thread-1")
    assert await provider.retrieve("query", thread_id="thread-1", limit=5) == []
    assert not await provider.add(
        MemoryAddRequest(
            target=MemoryTarget.USER,
            content="Prefer concise answers.",
            source="explicit_user_request",
        ),
        metadata={},
    )
    assert not await provider.delete("mem_1")
