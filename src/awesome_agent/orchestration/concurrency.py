import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager


class ConcurrencyController:
    def __init__(
        self,
        *,
        model_limit: int,
        tool_limit: int,
        sandbox_limit: int,
    ) -> None:
        self._model = asyncio.Semaphore(model_limit)
        self._tool = asyncio.Semaphore(tool_limit)
        self._sandbox = asyncio.Semaphore(sandbox_limit)

    @asynccontextmanager
    async def model_slot(self) -> AsyncIterator[None]:
        async with self._model:
            yield

    @asynccontextmanager
    async def tool_slot(self) -> AsyncIterator[None]:
        async with self._tool:
            yield

    @asynccontextmanager
    async def sandbox_slot(self) -> AsyncIterator[None]:
        async with self._sandbox:
            yield
