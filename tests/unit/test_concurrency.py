import asyncio

import pytest

from awesome_agent.orchestration.concurrency import ConcurrencyController


@pytest.mark.asyncio
async def test_model_concurrency_is_bounded() -> None:
    controller = ConcurrencyController(
        model_limit=2,
        tool_limit=3,
        sandbox_limit=1,
    )
    active = 0
    peak = 0

    async def worker() -> None:
        nonlocal active, peak
        async with controller.model_slot():
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1

    await asyncio.gather(*(worker() for _ in range(6)))

    assert peak == 2
