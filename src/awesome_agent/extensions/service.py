from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass

from awesome_agent.extensions.catalog import publish_catalog
from awesome_agent.extensions.hooks import ExtensionLifecycleHooks
from awesome_agent.extensions.models import ExtensionCatalog, ExtensionSourceSnapshot
from awesome_agent.extensions.sources import ExtensionSource


class ExtensionDiscoveryService:
    def __init__(
        self,
        sources: Iterable[ExtensionSource],
        *,
        hooks: ExtensionLifecycleHooks | None = None,
    ) -> None:
        self._sources = list(sources)
        self._hooks = hooks or ExtensionLifecycleHooks()
        self._source_health: dict[str, str] = {}

    async def publish(self) -> ExtensionCatalog:
        sources = []
        tools = []
        skills = []
        for source in self._sources:
            await self._hooks.before_extension_discovery(source.source_id)
            try:
                discovered = await source.discover()
            except Exception as error:
                await self._hooks.on_extension_discovery_error(
                    source_id=source.source_id,
                    error=error,
                )
                raise
            await self._hooks.after_extension_discovery(discovered)
            health_status = discovered.source.health.status.value
            previous = self._source_health.get(discovered.source.id)
            if previous != health_status:
                self._source_health[discovered.source.id] = health_status
                await self._hooks.on_extension_health_changed(discovered.source)
            sources.append(discovered.source)
            tools.extend(discovered.tools)
            skills.extend(discovered.skills)
        await self._hooks.before_extension_catalog_publish()
        catalog = publish_catalog(sources=sources, tools=tools, skills=skills)
        await self._hooks.after_extension_catalog_publish(catalog)
        return catalog


@dataclass(frozen=True)
class ExtensionHealthMonitorConfig:
    interval_seconds: float = 30.0
    max_backoff_seconds: float = 300.0
    failure_backoff_multiplier: float = 2.0

    def __post_init__(self) -> None:
        if self.interval_seconds <= 0:
            raise ValueError("interval_seconds must be greater than zero.")
        if self.max_backoff_seconds < self.interval_seconds:
            raise ValueError("max_backoff_seconds must be >= interval_seconds.")
        if self.failure_backoff_multiplier < 1:
            raise ValueError("failure_backoff_multiplier must be >= 1.")


class ExtensionHealthMonitor:
    """Background extension health poller.

    Health polling is intentionally outside AgentLoop middleware. It observes
    extension source availability and emits lifecycle hooks without publishing a
    new catalog or changing the catalog version pinned by an active run.
    """

    def __init__(
        self,
        sources: Iterable[ExtensionSource],
        *,
        hooks: ExtensionLifecycleHooks | None = None,
        config: ExtensionHealthMonitorConfig | None = None,
    ) -> None:
        self._sources = list(sources)
        self._hooks = hooks or ExtensionLifecycleHooks()
        self._config = config or ExtensionHealthMonitorConfig()
        self._source_health: dict[str, str] = {}
        self._backoff_seconds = {
            source.source_id: self._config.interval_seconds for source in self._sources
        }
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def poll_once(self) -> None:
        for source in self._sources:
            await self._poll_source(source)

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="extension-health-monitor")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop_event.set()
        await self._task
        self._task = None

    def current_backoff_seconds(self, source_id: str) -> float:
        return self._backoff_seconds[source_id]

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            await self.poll_once()
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=min(self._backoff_seconds.values(), default=0.0),
                )
            except TimeoutError:
                continue

    async def _poll_source(self, source: ExtensionSource) -> None:
        await self._hooks.before_extension_discovery(source.source_id)
        try:
            discovered = await source.discover()
        except Exception as error:
            await self._hooks.on_extension_discovery_error(
                source_id=source.source_id,
                error=error,
            )
            self._increase_backoff(source.source_id)
            return
        await self._hooks.after_extension_discovery(discovered)
        self._backoff_seconds[source.source_id] = self._config.interval_seconds
        await self._emit_health_change(discovered.source)

    async def _emit_health_change(self, source: ExtensionSourceSnapshot) -> None:
        health_status = source.health.status.value
        previous = self._source_health.get(source.id)
        if previous == health_status:
            return
        self._source_health[source.id] = health_status
        await self._hooks.on_extension_health_changed(source)

    def _increase_backoff(self, source_id: str) -> None:
        current = self._backoff_seconds[source_id]
        self._backoff_seconds[source_id] = min(
            self._config.max_backoff_seconds,
            current * self._config.failure_backoff_multiplier,
        )
