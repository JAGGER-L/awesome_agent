import asyncio

import pytest

from awesome_agent.domain.enums import RiskLevel
from awesome_agent.extensions.hooks import ExtensionLifecycleHooks
from awesome_agent.extensions.models import (
    ExtensionCatalog,
    ExtensionConfigError,
    ExtensionDiscoverySnapshot,
    ExtensionSourceConfig,
    ExtensionSourceSnapshot,
    ExtensionStaticToolConfig,
)
from awesome_agent.extensions.service import ExtensionDiscoveryService
from awesome_agent.extensions.sources import (
    ExtensionSourceFactory,
    StaticExtensionSource,
)


def test_static_extension_source_publishes_versioned_catalog() -> None:
    source = StaticExtensionSource(
        ExtensionSourceConfig(
            id="local-demo",
            type="static",
            trust="project",
            tools=[
                ExtensionStaticToolConfig(
                    name="demo.search",
                    description="Search demo content.",
                    risk_level=RiskLevel.LOW,
                    required_capabilities=["repository:read"],
                    input_schema={"type": "object"},
                )
            ],
        )
    )

    catalog = asyncio.run(ExtensionDiscoveryService([source]).publish())

    assert catalog.version
    assert catalog.tools[0].name == "extension.local-demo.demo.search"
    assert catalog.tools[0].source_id == "local-demo"
    assert catalog.sources[0].health.status == "healthy"


def test_extension_source_factory_rejects_unknown_type() -> None:
    with pytest.raises(ExtensionConfigError):
        ExtensionSourceFactory().create({"id": "bad", "type": "python_path"})


def test_catalog_version_changes_when_inventory_changes() -> None:
    first = asyncio.run(
        ExtensionDiscoveryService(
            [
                StaticExtensionSource(
                    ExtensionSourceConfig(
                        id="local-demo",
                        type="static",
                        trust="project",
                        tools=[],
                    )
                )
            ]
        ).publish()
    )
    second = asyncio.run(
        ExtensionDiscoveryService(
            [
                StaticExtensionSource(
                    ExtensionSourceConfig(
                        id="local-demo",
                        type="static",
                        trust="project",
                        tools=[
                            ExtensionStaticToolConfig(
                                name="demo.search",
                                description="Search demo content.",
                                risk_level=RiskLevel.LOW,
                                required_capabilities=["repository:read"],
                                input_schema={"type": "object"},
                            )
                        ],
                    )
                )
            ]
        ).publish()
    )

    assert first.version != second.version


def test_extension_discovery_service_emits_lifecycle_hooks() -> None:
    events: list[str] = []

    class RecordingHooks(ExtensionLifecycleHooks):
        async def before_extension_discovery(self, source_id: str) -> None:
            events.append(f"before:{source_id}")

        async def after_extension_discovery(
            self,
            discovered: ExtensionDiscoverySnapshot,
        ) -> None:
            events.append(f"after:{discovered.source.id}")

        async def on_extension_health_changed(
            self,
            source: ExtensionSourceSnapshot,
        ) -> None:
            events.append(f"health:{source.id}:{source.health.status.value}")

        async def before_extension_catalog_publish(self) -> None:
            events.append("before_publish")

        async def after_extension_catalog_publish(
            self,
            catalog: ExtensionCatalog,
        ) -> None:
            events.append(f"publish:{catalog.version}")

    source = StaticExtensionSource(
        ExtensionSourceConfig(id="local-demo", type="static", trust="project")
    )

    catalog = asyncio.run(
        ExtensionDiscoveryService([source], hooks=RecordingHooks()).publish()
    )

    assert events == [
        "before:local-demo",
        "after:local-demo",
        "health:local-demo:healthy",
        "before_publish",
        f"publish:{catalog.version}",
    ]
