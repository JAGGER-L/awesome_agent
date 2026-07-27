from __future__ import annotations

import asyncio
import contextvars
import math
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import AbstractAsyncContextManager, AsyncExitStack, contextmanager

from awesome_agent.modeling import (
    GatewayEvent,
    GatewayFactory,
    ModelGateway,
    ModelRequest,
    ModelTurn,
    ProviderId,
    SelectedModel,
)


class RuntimeResources:
    """Own one workspace runtime generation's reusable asynchronous resources."""

    def __init__(self, *, close_timeout_seconds: float = 5.0) -> None:
        if close_timeout_seconds <= 0 or not math.isfinite(close_timeout_seconds):
            raise ValueError(
                "Runtime resource close timeout must be finite and positive."
            )
        self._generation = object()
        self._close_timeout_seconds = close_timeout_seconds
        self._stack = AsyncExitStack()
        self._gateway_factory: GatewayFactory | None = None
        self._gateways: dict[tuple[ProviderId, str], ModelGateway] = {}
        self._reader_count = 0
        self._readers_idle = asyncio.Event()
        self._readers_idle.set()
        self._close_task: asyncio.Task[None] | None = None

    @property
    def generation(self) -> object:
        """An identity unique to this resource generation."""

        return self._generation

    @property
    def reader_count(self) -> int:
        return self._reader_count

    @property
    def closed(self) -> bool:
        task = self._close_task
        return task is not None and task.done() and not task.cancelled()

    async def enter_async_context[ResourceT](
        self,
        manager: AbstractAsyncContextManager[ResourceT],
    ) -> ResourceT:
        self._require_open()
        return await self._stack.enter_async_context(manager)

    def push_async_callback(
        self,
        callback: Callable[..., Awaitable[object]],
        /,
        *args: object,
        **kwargs: object,
    ) -> None:
        self._require_open()
        self._stack.push_async_callback(callback, *args, **kwargs)

    def bind_gateway_factory(self, factory: GatewayFactory) -> GatewayFactory:
        self._require_open()
        if self._gateway_factory is not None:
            raise RuntimeError("Runtime gateway factory is already bound.")
        self._gateway_factory = factory
        return self.gateway

    def gateway(self, provider: ProviderId, model: str) -> ModelGateway:
        self._require_open()
        factory = self._gateway_factory
        if factory is None:
            raise RuntimeError("Runtime gateway factory is not bound.")
        key = (provider, model)
        gateway = self._gateways.get(key)
        if gateway is None:
            gateway = factory(provider, model)
            self._gateways[key] = gateway
        return gateway

    async def complete(
        self,
        selected: SelectedModel,
        request: ModelRequest,
    ) -> ModelTurn:
        return await self.gateway(selected.provider, selected.model).complete(
            selected,
            request,
        )

    async def stream(
        self,
        selected: SelectedModel,
        request: ModelRequest,
    ) -> AsyncIterator[GatewayEvent]:
        async for event in self.gateway(selected.provider, selected.model).stream(
            selected,
            request,
        ):
            yield event

    @contextmanager
    def reader(self) -> Iterator[None]:
        self._require_open()
        self._reader_count += 1
        self._readers_idle.clear()
        try:
            yield
        finally:
            self._reader_count -= 1
            if self._reader_count == 0:
                self._readers_idle.set()

    async def aclose(self) -> None:
        close_task = self._close_task
        if close_task is None:
            close_task = asyncio.create_task(
                self._drain_and_close(),
                name="workspace-runtime-resources-close",
                context=contextvars.Context(),
            )
            self._close_task = close_task
        await asyncio.shield(close_task)

    async def _drain_and_close(self) -> None:
        await self._readers_idle.wait()
        self._gateways.clear()
        async with asyncio.timeout(self._close_timeout_seconds):
            await self._stack.aclose()

    def _require_open(self) -> None:
        if self._close_task is not None:
            raise RuntimeError("Runtime resources are closing or closed.")


__all__ = ["RuntimeResources"]
