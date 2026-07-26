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


class StorageContractError(RuntimeError):
    """The installed Core does not satisfy the release storage contract."""


_MAX_INVENTORY_ENTRIES = 4_096
_MAX_INVENTORY_FILE_BYTES = 16 * 1024 * 1024
_MAX_INVENTORY_TOTAL_BYTES = 64 * 1024 * 1024


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


def _verify_migration_registry(storage_module: ModuleType) -> None:
    expected_floor = 7
    expected_current = 7
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
    if migrations:
        raise StorageContractError("Schema 7 wheel unexpectedly publishes migrations")
    if registry.path_from(expected_current) != ():
        raise StorageContractError("current schema migration path is invalid")
    if registry.path_from(expected_floor - 1) is not None:
        raise StorageContractError("pre-floor schema unexpectedly has a migration path")


def verify_storage_contract(
    storage_module: ModuleType,
    paths_module: ModuleType,
    root: Path,
) -> None:
    expected_schema = 7
    if expected_schema != storage_module.APPLICATION_SCHEMA_VERSION:
        raise StorageContractError("wheel schema version is invalid")
    _verify_migration_registry(storage_module)

    fresh = root / "fresh-state" / "application.db"
    storage_module.initialize_application_database(fresh)
    with closing(sqlite3.connect(fresh)) as connection:
        observed_schema = int(connection.execute("PRAGMA user_version").fetchone()[0])
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    if observed_schema != expected_schema:
        raise StorageContractError("fresh database schema is invalid")
    required_tables = {"trusted_workspaces", "threads", "turns", "tool_activities"}
    if not required_tables.issubset(tables):
        raise StorageContractError("fresh database tables are incomplete")

    for found_schema, expected_direction in (
        (2, "migration_unavailable"),
        (6, "migration_unavailable"),
        (8, "newer"),
    ):
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
    verify_storage_contract(storage_module, paths_module, arguments.contract_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
