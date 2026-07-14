from __future__ import annotations

import argparse
import importlib
import shutil
import sqlite3
import subprocess
import sys
from collections.abc import Sequence
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


def _file_inventory(directory: Path) -> dict[str, bytes]:
    return {
        path.name: path.read_bytes()
        for path in sorted(directory.iterdir())
        if path.is_file()
    }


def _write_versioned_database(path: Path, version: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(f"PRAGMA user_version = {version}")


def _read_schema(path: Path) -> int:
    with closing(sqlite3.connect(path)) as connection:
        return int(connection.execute("PRAGMA user_version").fetchone()[0])


def verify_storage_contract(
    storage_module: ModuleType,
    paths_module: ModuleType,
    root: Path,
) -> None:
    expected_schema = 7
    if expected_schema != storage_module.APPLICATION_SCHEMA_VERSION:
        raise BundleVerificationError("wheel schema version is invalid")

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
        raise BundleVerificationError("fresh database schema is invalid")
    required_tables = {"trusted_workspaces", "threads", "turns", "tool_activities"}
    if not required_tables.issubset(tables):
        raise BundleVerificationError("fresh database tables are incomplete")

    for found_schema, expected_direction in ((2, "older"), (6, "older"), (8, "newer")):
        incompatible = root / f"schema-{found_schema}" / "application.db"
        _write_versioned_database(incompatible, found_schema)
        before = _file_inventory(incompatible.parent)
        preflight = storage_module.inspect_application_state(incompatible)
        if (
            preflight.found_schema != found_schema
            or preflight.expected_schema != expected_schema
            or preflight.compatibility.value != expected_direction
        ):
            raise BundleVerificationError(
                "incompatible schema classification is invalid"
            )
        try:
            storage_module.initialize_application_database(incompatible)
        except storage_module.ApplicationSchemaMismatch as error:
            if (
                error.found != found_schema
                or error.expected != expected_schema
                or error.direction.value != expected_direction
            ):
                raise BundleVerificationError(
                    "incompatible schema diagnostic is invalid"
                ) from error
        else:
            raise BundleVerificationError("incompatible schema was not rejected")
        if _file_inventory(incompatible.parent) != before:
            raise BundleVerificationError("incompatible state was mutated")

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
        storage_module.reset_local_state(paths, lease)

    if _read_schema(paths.application_db) != expected_schema:
        raise BundleVerificationError("reset did not create the current schema")
    if paths.checkpoint_db.exists() or paths.change_journal_dir.exists():
        raise BundleVerificationError("reset retained discarded state")
    if any(path.read_bytes() != content for path, content in preserved.items()):
        raise BundleVerificationError("reset mutated preserved user data")


def _load_wheel_modules(
    wheel: Path,
    import_root: Path,
) -> tuple[ModuleType, ModuleType, ModuleType]:
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
        storage_module = importlib.import_module("awesome_agent.storage")
        paths_module = importlib.import_module("awesome_agent.paths")
    except Exception as error:
        raise BundleVerificationError("wheel import failed") from error
    finally:
        sys.path.remove(str(import_root))
        for name in list(sys.modules):
            if name == "awesome_agent" or name.startswith("awesome_agent."):
                del sys.modules[name]
        sys.modules.update(previous_modules)
    return version_module, storage_module, paths_module


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
        with ZipFile(wheels[0]) as wheel_archive:
            module_paths = {
                Path(name).as_posix().casefold()
                for name in wheel_archive.namelist()
                if name.endswith(".py")
            }
        if any(
            "/migrations/" in path or Path(path).stem in {"migration", "migrations"}
            for path in module_paths
        ):
            raise BundleVerificationError("wheel contains a migration module")

        version_module, storage_module, paths_module = _load_wheel_modules(
            wheels[0], root / "wheel-import"
        )
        if expected_version != version_module.PRODUCT_VERSION:
            raise BundleVerificationError("wheel product version is invalid")
        verify_storage_contract(
            storage_module,
            paths_module,
            root / "storage-contract",
        )
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
