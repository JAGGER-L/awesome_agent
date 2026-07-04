from pathlib import Path

import pytest

from awesome_agent.extensions.catalog import empty_extension_catalog, publish_catalog
from awesome_agent.extensions.catalog_store import (
    CatalogSnapshotMissing,
    InMemoryExtensionCatalogStore,
    LocalExtensionCatalogStore,
)
from awesome_agent.extensions.models import ExtensionSkillInventoryItem


def test_in_memory_catalog_store_round_trips_active_catalog() -> None:
    catalog = publish_catalog(
        sources=[],
        tools=[],
        skills=[
            ExtensionSkillInventoryItem(
                id="repo",
                source_id="project",
                version="1",
            )
        ],
    )
    store = InMemoryExtensionCatalogStore()

    store.put(catalog, active=True)

    assert store.active().version == catalog.version
    assert store.get(catalog.version).skills[0].id == "repo"


def test_catalog_store_raises_for_missing_snapshot() -> None:
    store = InMemoryExtensionCatalogStore()
    store.put(empty_extension_catalog(), active=True)

    with pytest.raises(CatalogSnapshotMissing, match="missing"):
        store.get("missing")


def test_local_catalog_store_persists_snapshots(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    catalog = publish_catalog(
        sources=[],
        tools=[],
        skills=[
            ExtensionSkillInventoryItem(
                id="repo",
                source_id="project",
                version="1",
            )
        ],
    )

    first = LocalExtensionCatalogStore(path)
    first.put(catalog, active=True)
    first.close()

    second = LocalExtensionCatalogStore(path)
    try:
        assert second.active().version == catalog.version
        assert second.get(catalog.version).skills[0].id == "repo"
    finally:
        second.close()
