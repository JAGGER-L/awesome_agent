from __future__ import annotations

import sqlite3
from pathlib import Path

from awesome_agent.extensions.catalog import empty_extension_catalog
from awesome_agent.extensions.models import ExtensionCatalog


class CatalogSnapshotMissing(KeyError):
    def __init__(self, version: str) -> None:
        super().__init__(f"catalog_snapshot_missing: {version}")
        self.version = version


class InMemoryExtensionCatalogStore:
    def __init__(self, catalog: ExtensionCatalog | None = None) -> None:
        self._catalogs: dict[str, ExtensionCatalog] = {}
        self._active_version: str | None = None
        if catalog is not None:
            self.put(catalog, active=True)

    def put(self, catalog: ExtensionCatalog, *, active: bool = False) -> None:
        self._catalogs[catalog.version] = catalog
        if active or self._active_version is None:
            self._active_version = catalog.version

    def get(self, version: str) -> ExtensionCatalog:
        try:
            return self._catalogs[version]
        except KeyError as error:
            raise CatalogSnapshotMissing(version) from error

    def active(self) -> ExtensionCatalog:
        if self._active_version is None:
            catalog = empty_extension_catalog()
            self.put(catalog, active=True)
        return self.get(self._active_version)


class LocalExtensionCatalogStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._ensure_schema()

    def close(self) -> None:
        self._connection.close()

    def put(self, catalog: ExtensionCatalog, *, active: bool = False) -> None:
        with self._connection:
            self._connection.execute(
                """
                INSERT OR REPLACE INTO extension_catalog_snapshots
                  (version, published_at, payload_json)
                VALUES (?, ?, ?)
                """,
                (
                    catalog.version,
                    catalog.published_at.isoformat(),
                    catalog.model_dump_json(),
                ),
            )
            if active:
                self._connection.execute(
                    """
                    INSERT OR REPLACE INTO local_runtime_metadata (key, value)
                    VALUES ('active_extension_catalog_version', ?)
                    """,
                    (catalog.version,),
                )

    def get(self, version: str) -> ExtensionCatalog:
        row = self._connection.execute(
            """
            SELECT payload_json FROM extension_catalog_snapshots
            WHERE version = ?
            """,
            (version,),
        ).fetchone()
        if row is None:
            raise CatalogSnapshotMissing(version)
        return ExtensionCatalog.model_validate_json(row["payload_json"])

    def active(self) -> ExtensionCatalog:
        row = self._connection.execute(
            """
            SELECT value FROM local_runtime_metadata
            WHERE key = 'active_extension_catalog_version'
            """
        ).fetchone()
        if row is None:
            catalog = empty_extension_catalog()
            self.put(catalog, active=True)
            return catalog
        return self.get(str(row["value"]))

    def _ensure_schema(self) -> None:
        with self._connection:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS local_runtime_metadata (
                  key TEXT PRIMARY KEY,
                  value TEXT NOT NULL
                )
                """
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS extension_catalog_snapshots (
                  version TEXT PRIMARY KEY,
                  published_at TEXT NOT NULL,
                  payload_json TEXT NOT NULL
                )
                """
            )
