from __future__ import annotations

import argparse
import os
import sqlite3
import stat
from contextlib import closing
from pathlib import Path
from types import ModuleType
from typing import Protocol, cast


class _VersionModule(Protocol):
    PRODUCT_VERSION: str


class _SchemaProbe(Protocol):
    def __call__(self, connection: sqlite3.Connection) -> None: ...


class _MigrationProbe(Protocol):
    def __call__(
        self,
        storage_module: ModuleType,
        migrations_module: ModuleType,
        root: Path,
    ) -> None: ...


class StorageContractError(RuntimeError):
    """The installed Core does not satisfy the release storage contract."""


_MAX_INVENTORY_ENTRIES = 4_096
_MAX_INVENTORY_FILE_BYTES = 16 * 1024 * 1024
_MAX_INVENTORY_TOTAL_BYTES = 64 * 1024 * 1024


def _verify_schema_8(connection: sqlite3.Connection) -> None:
    expected_columns = {
        "trusted_workspaces": ("workspace_key", "canonical_path", "trusted_at"),
        "change_sets": (
            "change_set_id",
            "workspace_key",
            "session_id",
            "turn_id",
            "lifecycle",
            "reversibility",
            "payload_json",
            "created_at",
            "sealed_at",
        ),
        "pending_mutations": (
            "pending_id",
            "change_set_id",
            "relative_path",
            "kind",
            "node_type",
            "before_hash",
            "before_blob",
            "before_mode",
            "intended_after_hash",
            "intended_after_blob",
            "intended_after_mode",
            "created_at",
        ),
        "threads": (
            "thread_id",
            "workspace_key",
            "title",
            "title_source",
            "current_model",
            "thinking_enabled",
            "skill_mode",
            "lineage_json",
            "created_at",
            "updated_at",
        ),
        "thread_entries": (
            "entry_id",
            "thread_id",
            "sequence",
            "kind",
            "content",
            "client_message_id",
            "metadata_json",
            "created_at",
        ),
        "turns": (
            "turn_id",
            "thread_id",
            "checkpoint_key",
            "status",
            "provider",
            "model",
            "thinking_enabled",
            "skill_mode",
            "budgets_json",
            "user_entry_id",
            "assistant_entry_id",
            "usage_json",
            "termination_reason",
            "error_code",
            "context_manifest_json",
            "created_at",
            "updated_at",
            "completed_at",
        ),
        "thread_summaries": (
            "thread_id",
            "content",
            "content_hash",
            "covered_entry_sequence",
            "covered_turn_count",
            "estimated_tokens",
            "provider",
            "model",
            "updated_at",
        ),
        "tool_activities": (
            "activity_id",
            "thread_id",
            "turn_id",
            "operation_id",
            "call_id",
            "sequence",
            "origin",
            "tool_name",
            "outcome",
            "input_summary",
            "result_summary",
            "error_code",
            "duration_ms",
            "change_set_id",
            "created_at",
        ),
        "mcp_enablements": (
            "workspace_key",
            "server_id",
            "config_hash",
            "enabled_at",
        ),
    }
    observed_tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    if observed_tables != set(expected_columns):
        raise StorageContractError("fresh database tables are incomplete")
    for table, expected in expected_columns.items():
        observed = {
            str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")
        }
        if observed != set(expected):
            raise StorageContractError(f"fresh {table} schema is incomplete")
    expected_indexes = {
        "idx_change_sets_workspace_created",
        "idx_thread_entries_sequence",
        "idx_threads_workspace_updated",
        "idx_tool_activities_operation_call",
        "idx_tool_activities_thread_created",
        "idx_tool_activities_thread_operation",
        "idx_tool_activities_thread_turn",
        "idx_turns_one_in_progress",
        "idx_turns_thread_created",
    }
    observed_indexes = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'index' AND name NOT LIKE 'sqlite_%'"
        )
    }
    if observed_indexes != expected_indexes:
        raise StorageContractError("fresh database indexes are incomplete")
    if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise StorageContractError("fresh database integrity check failed")


_APPLICATION_SCHEMA_PROBES: dict[int, _SchemaProbe] = {8: _verify_schema_8}


def _seed_schema_7(storage_module: ModuleType, path: Path) -> None:
    storage_module.initialize_application_database(path)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("ALTER TABLE threads DROP COLUMN lineage_json")
        connection.execute(
            """
            INSERT INTO threads (
                thread_id, workspace_key, title, title_source, current_model,
                thinking_enabled, skill_mode, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "thread_existing",
                "workspace_existing",
                "Preserved",
                "manual",
                "deepseek/deepseek-v4-flash",
                1,
                "auto",
                "2026-07-27T00:00:00+00:00",
                "2026-07-27T00:00:00+00:00",
            ),
        )
        connection.execute("PRAGMA user_version = 7")
        connection.commit()


def _verify_preserved_thread(connection: sqlite3.Connection) -> None:
    row = connection.execute(
        "SELECT thread_id, title FROM threads WHERE thread_id = 'thread_existing'"
    ).fetchone()
    if row != ("thread_existing", "Preserved"):
        raise StorageContractError("schema migration did not preserve Thread data")


def _verify_schema_7_to_8_migration(
    storage_module: ModuleType,
    migrations_module: ModuleType,
    root: Path,
) -> None:
    migrated = root / "schema-7-migration" / "application.db"
    _seed_schema_7(storage_module, migrated)
    with closing(sqlite3.connect(migrated)) as connection:
        backup = migrations_module.migrate_application_database(connection, migrated)
    expected_backup = migrated.with_name("application.db.pre-migration.bak")
    if backup != expected_backup or not expected_backup.is_file():
        raise StorageContractError("schema migration backup is invalid")
    with closing(sqlite3.connect(migrated)) as connection:
        if int(connection.execute("PRAGMA user_version").fetchone()[0]) != 8:
            raise StorageContractError("schema migration did not publish schema 8")
        _verify_preserved_thread(connection)
        if connection.execute(
            "SELECT lineage_json FROM threads WHERE thread_id = 'thread_existing'"
        ).fetchone() != (None,):
            raise StorageContractError("schema migration lineage default is invalid")
        _verify_schema_8(connection)
    with closing(sqlite3.connect(expected_backup)) as connection:
        if int(connection.execute("PRAGMA user_version").fetchone()[0]) != 7:
            raise StorageContractError("schema migration backup identity is invalid")
        _verify_preserved_thread(connection)
        if "lineage_json" in {
            str(row[1]) for row in connection.execute("PRAGMA table_info(threads)")
        }:
            raise StorageContractError("schema migration backup was mutated")

    rolled_back = root / "schema-7-rollback" / "application.db"
    _seed_schema_7(storage_module, rolled_back)
    production_migration = storage_module.APPLICATION_MIGRATIONS.migrations[0]

    def fail_after_production_step(connection: object) -> None:
        production_migration.apply(connection)
        raise RuntimeError("injected release migration failure")

    failing_registry = storage_module.ApplicationMigrationRegistry(
        floor=7,
        current=8,
        migrations=(
            storage_module.ApplicationMigration(7, 8, fail_after_production_step),
        ),
    )
    try:
        with closing(sqlite3.connect(rolled_back)) as connection:
            migrations_module.migrate_application_database(
                connection,
                rolled_back,
                registry=failing_registry,
            )
    except storage_module.ApplicationMigrationStepError:
        pass
    else:
        raise StorageContractError("failed schema migration was not rejected")
    with closing(sqlite3.connect(rolled_back)) as connection:
        if int(connection.execute("PRAGMA user_version").fetchone()[0]) != 7:
            raise StorageContractError("failed schema migration did not roll back")
        _verify_preserved_thread(connection)
        if "lineage_json" in {
            str(row[1]) for row in connection.execute("PRAGMA table_info(threads)")
        }:
            raise StorageContractError("failed schema migration retained its DDL")


_APPLICATION_MIGRATION_PROBES: dict[tuple[int, int], _MigrationProbe] = {
    (7, 8): _verify_schema_7_to_8_migration
}


def _metadata_fingerprint(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_size,
        metadata.st_mtime_ns,
    )


def _read_stable_regular_file(path: Path, expected: os.stat_result) -> bytes:
    flags = os.O_RDONLY
    flags |= int(getattr(os, "O_BINARY", 0))
    flags |= int(getattr(os, "O_NOFOLLOW", 0))
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise StorageContractError("state inventory file is unreadable") from error
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or _metadata_fingerprint(
            opened
        ) != _metadata_fingerprint(expected):
            raise StorageContractError("state inventory changed while opening")
        if opened.st_size > _MAX_INVENTORY_FILE_BYTES:
            raise StorageContractError("state inventory file is too large")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            content = stream.read(_MAX_INVENTORY_FILE_BYTES + 1)
        closed_over = os.fstat(descriptor)
    except StorageContractError:
        raise
    except OSError as error:
        raise StorageContractError("state inventory file is unreadable") from error
    finally:
        os.close(descriptor)
    if len(content) > _MAX_INVENTORY_FILE_BYTES:
        raise StorageContractError("state inventory file is too large")
    if len(content) != closed_over.st_size or _metadata_fingerprint(
        closed_over
    ) != _metadata_fingerprint(opened):
        raise StorageContractError("state inventory changed while reading")
    return content


def _tree_inventory(directory: Path) -> dict[str, tuple[str, int, bytes]]:
    """Snapshot a bounded stable tree and never descend into observed links."""

    inventory: dict[str, tuple[str, int, bytes]] = {}
    try:
        root_metadata = os.lstat(directory)
    except OSError as error:
        raise StorageContractError("state inventory is unreadable") from error
    pending = [(directory, root_metadata)]
    total_bytes = 0
    while pending:
        current, expected_directory = pending.pop()
        try:
            opened_directory = os.lstat(current)
            if (
                current.is_symlink()
                or os.path.isjunction(current)
                or not stat.S_ISDIR(opened_directory.st_mode)
                or _metadata_fingerprint(opened_directory)
                != _metadata_fingerprint(expected_directory)
            ):
                raise StorageContractError("state inventory changed while opening")
            entries = sorted(os.scandir(current), key=lambda entry: entry.name)
        except OSError as error:
            raise StorageContractError("state inventory is unreadable") from error
        for entry in entries:
            if len(inventory) >= _MAX_INVENTORY_ENTRIES:
                raise StorageContractError("state inventory has too many entries")
            path = Path(entry.path)
            relative = path.relative_to(directory).as_posix()
            try:
                metadata = os.lstat(path)
            except OSError as error:
                raise StorageContractError("state inventory is unreadable") from error
            permissions = stat.S_IMODE(metadata.st_mode)
            if path.is_symlink() or os.path.isjunction(path):
                try:
                    target = os.fsencode(os.readlink(path))
                except OSError as error:
                    raise StorageContractError(
                        "state inventory link is unreadable"
                    ) from error
                try:
                    closed_over = os.lstat(path)
                except OSError as error:
                    raise StorageContractError(
                        "state inventory changed while reading"
                    ) from error
                if _metadata_fingerprint(closed_over) != _metadata_fingerprint(
                    metadata
                ):
                    raise StorageContractError("state inventory changed while reading")
                inventory[relative] = ("link", permissions, target)
                continue
            if stat.S_ISDIR(metadata.st_mode):
                inventory[relative] = ("directory", permissions, b"")
                pending.append((path, metadata))
                continue
            if stat.S_ISREG(metadata.st_mode):
                content = _read_stable_regular_file(path, metadata)
                total_bytes += len(content)
                if total_bytes > _MAX_INVENTORY_TOTAL_BYTES:
                    raise StorageContractError("state inventory is too large")
                inventory[relative] = ("file", permissions, content)
                continue
            inventory[relative] = ("special", metadata.st_mode, b"")
        try:
            closed_over_directory = os.lstat(current)
        except OSError as error:
            raise StorageContractError(
                "state inventory changed while reading"
            ) from error
        if _metadata_fingerprint(closed_over_directory) != _metadata_fingerprint(
            opened_directory
        ):
            raise StorageContractError("state inventory changed while reading")
    return inventory


def _write_versioned_database(path: Path, version: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(f"PRAGMA user_version = {version}")


def _read_schema(path: Path) -> int:
    with closing(sqlite3.connect(path)) as connection:
        return int(connection.execute("PRAGMA user_version").fetchone()[0])


def _verify_migration_registry(
    storage_module: ModuleType,
    expected_floor: int,
    expected_current: int,
) -> None:
    if expected_floor != storage_module.APPLICATION_SCHEMA_FLOOR:
        raise StorageContractError("wheel migration floor is invalid")
    registry = storage_module.APPLICATION_MIGRATIONS
    if registry.floor != expected_floor or registry.current != expected_current:
        raise StorageContractError("wheel migration registry identity is invalid")

    migrations = tuple(registry.migrations)
    sources = tuple(migration.from_schema for migration in migrations)
    targets = tuple(migration.to_schema for migration in migrations)
    expected_sources = tuple(range(expected_floor, expected_current))
    expected_targets = tuple(range(expected_floor + 1, expected_current + 1))
    if sources != expected_sources or targets != expected_targets:
        raise StorageContractError("wheel migration registry is not one linear chain")
    if len(set(sources)) != len(sources):
        raise StorageContractError("wheel migration registry contains a branch")
    if registry.path_from(expected_floor) != migrations:
        raise StorageContractError("migration floor path is invalid")
    if registry.path_from(expected_current) != ():
        raise StorageContractError("current schema migration path is invalid")
    if registry.path_from(expected_floor - 1) is not None:
        raise StorageContractError("pre-floor schema unexpectedly has a migration path")


def verify_storage_contract(
    storage_module: ModuleType,
    migrations_module: ModuleType,
    paths_module: ModuleType,
    root: Path,
    *,
    expected_schema_floor: int,
    expected_schema_current: int,
) -> None:
    expected_schema = expected_schema_current
    probe = _APPLICATION_SCHEMA_PROBES.get(expected_schema)
    if probe is None:
        raise StorageContractError("wheel schema probe is unavailable")
    if expected_schema != storage_module.APPLICATION_SCHEMA_VERSION:
        raise StorageContractError("wheel schema version is invalid")
    _verify_migration_registry(
        storage_module,
        expected_schema_floor,
        expected_schema_current,
    )
    if expected_schema_floor < expected_schema_current:
        migration_probe = _APPLICATION_MIGRATION_PROBES.get(
            (expected_schema_floor, expected_schema_current)
        )
        if migration_probe is None:
            raise StorageContractError("wheel migration probe is unavailable")
        migration_probe(storage_module, migrations_module, root)

    fresh = root / "fresh-state" / "application.db"
    storage_module.initialize_application_database(fresh)
    with closing(sqlite3.connect(fresh)) as connection:
        observed_schema = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if observed_schema != expected_schema:
            raise StorageContractError("fresh database schema is invalid")
        probe(connection)

    pre_floor_schemas = tuple(
        sorted(
            {
                candidate
                for candidate in (
                    max(1, expected_schema_floor - 5),
                    expected_schema_floor - 1,
                )
                if 0 < candidate < expected_schema_floor
            }
        )
    )
    incompatible_schemas = [
        (schema, "migration_unavailable") for schema in pre_floor_schemas
    ]
    if expected_schema_floor < expected_schema_current:
        incompatible_schemas.append((expected_schema_floor, "migration_required"))
    incompatible_schemas.append((expected_schema_current + 1, "newer"))
    for found_schema, expected_direction in incompatible_schemas:
        incompatible = root / f"schema-{found_schema}" / "application.db"
        _write_versioned_database(incompatible, found_schema)
        before = _tree_inventory(incompatible.parent)
        preflight = storage_module.inspect_application_state(incompatible)
        if (
            preflight.found_schema != found_schema
            or preflight.expected_schema != expected_schema
            or preflight.compatibility.value != expected_direction
        ):
            raise StorageContractError("incompatible schema classification is invalid")
        try:
            storage_module.initialize_application_database(incompatible)
        except storage_module.ApplicationSchemaMismatch as error:
            if (
                error.found != found_schema
                or error.expected != expected_schema
                or error.direction.value != expected_direction
            ):
                raise StorageContractError(
                    "incompatible schema diagnostic is invalid"
                ) from error
        else:
            raise StorageContractError("incompatible schema was not rejected")
        if _tree_inventory(incompatible.parent) != before:
            raise StorageContractError("incompatible state was mutated")

    unknown = root / "schema-unknown" / "application.db"
    unknown.parent.mkdir(parents=True)
    with closing(sqlite3.connect(unknown)) as connection:
        connection.execute("CREATE TABLE preserved_marker (value TEXT NOT NULL)")
        connection.execute("INSERT INTO preserved_marker VALUES ('preserved')")
        connection.commit()
    before_unknown = _tree_inventory(unknown.parent)
    preflight = storage_module.inspect_application_state(unknown)
    if (
        preflight.found_schema != 0
        or preflight.expected_schema != expected_schema
        or preflight.compatibility.value != "unknown"
    ):
        raise StorageContractError("unknown schema classification is invalid")
    try:
        storage_module.initialize_application_database(unknown)
    except storage_module.ApplicationStateUnknown:
        pass
    else:
        raise StorageContractError("unknown schema was not rejected")
    if _tree_inventory(unknown.parent) != before_unknown:
        raise StorageContractError("unknown state was mutated")

    home = root / "reset-home"
    paths = paths_module.AwesomePaths.from_home(home)
    preserved = {
        paths.config_file: b"version: 1\n",
        paths.env_file: b"DEEPSEEK_API_KEY=preserved\n",
        paths.skills_dir / "review" / "SKILL.md": b"# Review\n",
        paths.user_memory_file: b"# User memory\n",
        paths.workspaces_dir / "workspace" / "MEMORY.md": b"# Workspace memory\n",
        paths.ui_file: b'{"theme":"aurora"}\n',
    }
    for path, content in preserved.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    _write_versioned_database(paths.application_db, 6)
    paths.checkpoint_db.write_bytes(b"discarded checkpoint")
    paths.change_journal_dir.mkdir(parents=True)
    (paths.change_journal_dir / "discarded").write_bytes(b"discarded change")

    with storage_module.StateLease.acquire(
        paths.home,
        storage_module.StateLeaseMode.EXCLUSIVE,
    ) as lease:
        storage_module.reset_local_state(lease)

    if _read_schema(paths.application_db) != expected_schema:
        raise StorageContractError("reset did not create the current schema")
    if paths.checkpoint_db.exists() or paths.change_journal_dir.exists():
        raise StorageContractError("reset retained discarded state")
    if any(path.read_bytes() != content for path, content in preserved.items()):
        raise StorageContractError("reset mutated preserved user data")


def _require_installed_module(module: ModuleType, environment: Path) -> None:
    module_file = getattr(module, "__file__", None)
    if not isinstance(module_file, str):
        raise StorageContractError("installed wheel module origin is unavailable")
    try:
        Path(module_file).resolve().relative_to(environment.resolve())
    except (OSError, ValueError) as error:
        raise StorageContractError(
            "installed wheel module escaped the clean environment"
        ) from error


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("expected_version")
    parser.add_argument("expected_schema_floor", type=int)
    parser.add_argument("expected_schema_current", type=int)
    parser.add_argument("environment", type=Path)
    parser.add_argument("contract_root", type=Path)
    arguments = parser.parse_args()

    import awesome_agent.paths as paths_module
    import awesome_agent.storage as storage_module
    import awesome_agent.storage.migrations as migrations_module
    import awesome_agent.version as version_module

    modules = (paths_module, storage_module, migrations_module, version_module)
    for module in modules:
        _require_installed_module(module, arguments.environment)
    version = cast(_VersionModule, version_module).PRODUCT_VERSION
    if version != arguments.expected_version:
        raise StorageContractError("installed wheel product version is invalid")
    verify_storage_contract(
        storage_module,
        migrations_module,
        paths_module,
        arguments.contract_root,
        expected_schema_floor=arguments.expected_schema_floor,
        expected_schema_current=arguments.expected_schema_current,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
