import asyncio

import pytest

from awesome_agent.domain.enums import RiskLevel
from awesome_agent.extensions.hooks import ExtensionLifecycleHooks
from awesome_agent.extensions.models import (
    ExtensionCatalog,
    ExtensionConfigError,
    ExtensionDiscoverySnapshot,
    ExtensionHealthSnapshot,
    ExtensionHealthStatus,
    ExtensionSourceConfig,
    ExtensionSourceSnapshot,
    ExtensionStaticToolConfig,
)
from awesome_agent.extensions.service import (
    ExtensionDiscoveryService,
    ExtensionHealthMonitor,
    ExtensionHealthMonitorConfig,
)
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


def test_health_monitor_polls_in_background_without_publishing_catalog() -> None:
    events: list[str] = []
    source = CountingSource()

    class RecordingHooks(ExtensionLifecycleHooks):
        async def before_extension_discovery(self, source_id: str) -> None:
            events.append(f"before:{source_id}")

        async def on_extension_health_changed(
            self,
            source: ExtensionSourceSnapshot,
        ) -> None:
            events.append(f"health:{source.id}:{source.health.status.value}")

        async def before_extension_catalog_publish(self) -> None:
            events.append("before_publish")

    async def run_monitor() -> None:
        monitor = ExtensionHealthMonitor(
            [source],
            hooks=RecordingHooks(),
            config=ExtensionHealthMonitorConfig(interval_seconds=0.01),
        )
        await monitor.start()
        await asyncio.sleep(0.03)
        await monitor.stop()

    asyncio.run(run_monitor())

    assert source.calls >= 1
    assert "before:local-demo" in events
    assert "health:local-demo:healthy" in events
    assert "before_publish" not in events


def test_extension_health_monitor_backs_off_after_failures_and_resets() -> None:
    source = FlakySource()

    async def run_monitor() -> tuple[float, float, float]:
        monitor = ExtensionHealthMonitor(
            [source],
            config=ExtensionHealthMonitorConfig(
                interval_seconds=1.0,
                max_backoff_seconds=3.0,
                failure_backoff_multiplier=2.0,
            ),
        )
        await monitor.poll_once()
        first_failure = monitor.current_backoff_seconds("local-demo")
        await monitor.poll_once()
        second_failure = monitor.current_backoff_seconds("local-demo")
        await monitor.poll_once()
        after_success = monitor.current_backoff_seconds("local-demo")
        return first_failure, second_failure, after_success

    assert asyncio.run(run_monitor()) == (2.0, 3.0, 1.0)


class CountingSource:
    def __init__(self) -> None:
        self.calls = 0

    @property
    def source_id(self) -> str:
        return "local-demo"

    async def discover(self) -> ExtensionDiscoverySnapshot:
        self.calls += 1
        return ExtensionDiscoverySnapshot(source=_healthy_source_snapshot())


class FlakySource:
    def __init__(self) -> None:
        self.calls = 0

    @property
    def source_id(self) -> str:
        return "local-demo"

    async def discover(self) -> ExtensionDiscoverySnapshot:
        self.calls += 1
        if self.calls < 3:
            raise RuntimeError("temporary extension outage")
        return ExtensionDiscoverySnapshot(source=_healthy_source_snapshot())


def _healthy_source_snapshot() -> ExtensionSourceSnapshot:
    return ExtensionSourceSnapshot(
        id="local-demo",
        type="static",
        trust="project",
        health=ExtensionHealthSnapshot(status=ExtensionHealthStatus.HEALTHY),
    )
