from __future__ import annotations

from awesome_agent.extensions.models import (
    ExtensionCatalog,
    ExtensionDiscoverySnapshot,
    ExtensionSourceConfig,
    ExtensionSourceSnapshot,
)


class ExtensionLifecycleHooks:
    """Observer hooks for the extension control-plane lifecycle.

    These hooks are intentionally separate from AgentLoop middleware. They
    observe catalog construction and health, but they cannot expose or execute
    extension tools.
    """

    async def before_extension_config_load(self) -> None:
        return None

    async def after_extension_config_load(
        self,
        configs: list[ExtensionSourceConfig],
    ) -> None:
        return None

    async def before_extension_discovery(self, source_id: str) -> None:
        return None

    async def after_extension_discovery(
        self,
        discovered: ExtensionDiscoverySnapshot,
    ) -> None:
        return None

    async def on_extension_discovery_error(
        self,
        *,
        source_id: str,
        error: Exception,
    ) -> None:
        return None

    async def before_extension_catalog_publish(self) -> None:
        return None

    async def after_extension_catalog_publish(self, catalog: ExtensionCatalog) -> None:
        return None

    async def on_extension_health_changed(
        self,
        source: ExtensionSourceSnapshot,
    ) -> None:
        return None
