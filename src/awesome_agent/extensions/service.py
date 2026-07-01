from __future__ import annotations

from collections.abc import Iterable

from awesome_agent.extensions.catalog import publish_catalog
from awesome_agent.extensions.hooks import ExtensionLifecycleHooks
from awesome_agent.extensions.models import ExtensionCatalog
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
