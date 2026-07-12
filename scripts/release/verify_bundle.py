from __future__ import annotations

import argparse
import importlib
import shutil
import sqlite3
import subprocess
import sys
from collections.abc import Mapping, Sequence
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from types import ModuleType
from zipfile import ZipFile


class BundleVerificationError(RuntimeError):
    """The release bundle does not satisfy its upgrade contract."""


def find_payload(archive: ZipFile, expected_version: str) -> str:
    prefix = f"awesome-{expected_version}/"
    names = archive.namelist()
    if not names or any(not name.startswith(prefix) for name in names):
        raise BundleVerificationError("bundle prefix is invalid")
    return prefix


def create_historical_schema_five(
    path: Path,
    migrations: Mapping[int, object],
) -> None:
    expected_versions = set(range(1, 6))
    if set(migrations) != expected_versions or any(
        not isinstance(migrations[version], str) for version in expected_versions
    ):
        raise BundleVerificationError("historical migrations 1-5 must be SQL")
    migration_three = migrations[3]
    assert isinstance(migration_three, str)
    if "client_message_id" in migration_three:
        raise BundleVerificationError("historical migration 3 has current columns")

    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        for version in range(1, 6):
            migration = migrations[version]
            assert isinstance(migration, str)
            connection.executescript(
                "BEGIN IMMEDIATE;\n"
                f"{migration.rstrip().rstrip(';')};\n"
                f"PRAGMA user_version = {version};\n"
                "COMMIT;"
            )
        timestamp = "2026-01-01T00:00:00+00:00"
        connection.execute(
            "INSERT INTO trusted_workspaces VALUES (?, ?, ?)",
            ("workspace_release_smoke", "/release-smoke", timestamp),
        )
        connection.execute(
            """
            INSERT INTO threads (
                thread_id, workspace_key, title, current_model,
                thinking_enabled, skill_mode, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "thread_release_smoke",
                "workspace_release_smoke",
                "Release smoke",
                None,
                0,
                "off",
                timestamp,
                timestamp,
            ),
        )
        connection.execute(
            """
            INSERT INTO thread_entries (
                entry_id, thread_id, sequence, kind, content,
                metadata_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "entry_release_smoke",
                "thread_release_smoke",
                1,
                "user_message",
                "historical message",
                "{}",
                timestamp,
            ),
        )
        connection.commit()


def verify_upgraded_database(
    path: Path,
    *,
    expected_schema_version: int,
) -> None:
    with closing(sqlite3.connect(path)) as connection:
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version != expected_schema_version:
            raise BundleVerificationError("unexpected schema version")
        trust = connection.execute(
            "SELECT workspace_key FROM trusted_workspaces"
        ).fetchone()
        thread = connection.execute("SELECT thread_id FROM threads").fetchone()
        entry = connection.execute(
            """
            SELECT entry_id, kind, content, client_message_id
            FROM thread_entries WHERE entry_id = 'entry_release_smoke'
            """
        ).fetchone()
        if trust != ("workspace_release_smoke",) or thread != ("thread_release_smoke",):
            raise BundleVerificationError("historical state was not preserved")
        if entry != (
            "entry_release_smoke",
            "user_message",
            "historical message",
            "client_legacy_entry_release_smoke",
        ):
            raise BundleVerificationError("migrated client identity is invalid")


def _load_wheel_modules(
    wheel: Path,
    import_root: Path,
) -> tuple[ModuleType, ModuleType]:
    with ZipFile(wheel) as archive:
        archive.extractall(import_root)
    previous_modules = {
        name: module
        for name, module in sys.modules.items()
        if name == "awesome_agent" or name.startswith("awesome_agent.")
    }
    for name in previous_modules:
        del sys.modules[name]
    sys.path.insert(0, str(import_root))
    try:
        version_module = importlib.import_module("awesome_agent.version")
        database_module = importlib.import_module("awesome_agent.storage.database")
    except Exception as error:
        raise BundleVerificationError("wheel import failed") from error
    finally:
        sys.path.remove(str(import_root))
        for name in list(sys.modules):
            if name == "awesome_agent" or name.startswith("awesome_agent."):
                del sys.modules[name]
        sys.modules.update(previous_modules)
    return version_module, database_module


def resolve_executable(name: str) -> str:
    for candidate in (name, f"{name}.cmd", f"{name}.exe"):
        resolved = shutil.which(candidate)
        if resolved is not None:
            return resolved
    raise BundleVerificationError(f"{name} runtime is unavailable")


def _verify_tui(tui: Path, expected_version: str) -> None:
    try:
        install = subprocess.run(
            [resolve_executable("npm"), "ci", "--omit=dev", "--ignore-scripts"],
            cwd=tui,
            capture_output=True,
            text=True,
            check=False,
        )
        if install.returncode != 0:
            raise BundleVerificationError("TUI dependency installation failed")
        result = subprocess.run(
            [
                resolve_executable("node"),
                str(tui / "dist" / "cli" / "index.js"),
                "--version",
            ],
            cwd=tui,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as error:
        raise BundleVerificationError("Node runtime is unavailable") from error
    if result.returncode != 0 or result.stdout != f"{expected_version}\n":
        raise BundleVerificationError("TUI version check failed")


def verify_release_bundle(bundle: Path, expected_version: str) -> None:
    if not bundle.is_file():
        raise BundleVerificationError("bundle is missing")
    with TemporaryDirectory(prefix="awesome-release-verify-") as temporary:
        root = Path(temporary)
        try:
            with ZipFile(bundle) as archive:
                prefix = find_payload(archive, expected_version)
                archive.extractall(root)
        except BundleVerificationError:
            raise
        except Exception as error:
            raise BundleVerificationError("bundle extraction failed") from error

        payload = root / prefix.rstrip("/")
        wheels = list((payload / "core").glob("*.whl"))
        expected_fragment = f"awesome_agent-{expected_version}"
        if len(wheels) != 1 or expected_fragment not in wheels[0].name:
            raise BundleVerificationError("bundle wheel identity is invalid")
        version_module, database_module = _load_wheel_modules(
            wheels[0], root / "wheel-import"
        )
        if expected_version != version_module.PRODUCT_VERSION:
            raise BundleVerificationError("wheel product version is invalid")
        if database_module.APPLICATION_SCHEMA_VERSION != 6:
            raise BundleVerificationError("wheel schema version is invalid")

        database = root / "application.db"
        create_historical_schema_five(
            database,
            {version: database_module._MIGRATIONS[version] for version in range(1, 6)},
        )
        database_module.initialize_application_database(database)
        verify_upgraded_database(database, expected_schema_version=6)
        with closing(sqlite3.connect(database)) as connection:
            connection.execute(
                """
                INSERT INTO thread_entries (
                    entry_id, thread_id, sequence, kind, content,
                    metadata_json, created_at, client_message_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "entry_release_new",
                    "thread_release_smoke",
                    2,
                    "user_message",
                    "new message",
                    "{}",
                    "2026-01-01T00:00:01+00:00",
                    "client_release_new",
                ),
            )
            connection.commit()
        _verify_tui(payload / "tui", expected_version)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle", type=Path)
    parser.add_argument("version")
    arguments = parser.parse_args(argv)
    try:
        verify_release_bundle(arguments.bundle, arguments.version)
    except BundleVerificationError as error:
        parser.exit(1, f"bundle verification failed: {error}\n")
    print(f"verified awesome-{arguments.version}.zip")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
