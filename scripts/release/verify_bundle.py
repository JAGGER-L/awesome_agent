from __future__ import annotations

import argparse
import importlib
import shutil
import sqlite3
import subprocess
import sys
from collections.abc import Sequence
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


def verify_storage_contract(database_module: ModuleType, root: Path) -> None:
    expected_schema = 2
    if expected_schema != database_module.APPLICATION_SCHEMA_VERSION:
        raise BundleVerificationError("wheel schema version is invalid")

    fresh = root / "fresh-state" / "application.db"
    database_module.initialize_application_database(fresh)
    with sqlite3.connect(fresh) as connection:
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

    incompatible = root / "incompatible-state" / "application.db"
    incompatible.parent.mkdir(parents=True)
    with sqlite3.connect(incompatible) as connection:
        connection.execute("PRAGMA user_version = 1")
    before = _file_inventory(incompatible.parent)
    try:
        database_module.initialize_application_database(incompatible)
    except database_module.ApplicationSchemaMismatch as error:
        if error.found != 1 or error.expected != expected_schema:
            raise BundleVerificationError(
                "incompatible schema diagnostic is invalid"
            ) from error
    else:
        raise BundleVerificationError("incompatible schema was not rejected")
    if _file_inventory(incompatible.parent) != before:
        raise BundleVerificationError("incompatible state was mutated")


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
        verify_storage_contract(database_module, root / "storage-contract")
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
