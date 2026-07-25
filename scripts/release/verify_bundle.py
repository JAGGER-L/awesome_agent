from __future__ import annotations

import argparse
import hashlib
import importlib
import shutil
import sqlite3
import stat
import subprocess
import sys
from collections.abc import Sequence
from contextlib import closing
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from types import ModuleType
from zipfile import ZipFile

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.release.contracts import (
    MAX_RELEASE_REQUIREMENTS_BYTES,
    ReleaseContractError,
    validate_locked_requirements,
    validate_release_wheel,
)


class BundleVerificationError(RuntimeError):
    """The release bundle does not satisfy its upgrade contract."""


_MAX_CHECKSUM_MANIFEST_BYTES = 4 * 1024


def _is_plain_file(path: Path) -> bool:
    try:
        return stat.S_ISREG(path.stat(follow_symlinks=False).st_mode)
    except OSError:
        return False


def verify_release_assets(release: Path, expected_version: str) -> None:
    expected_assets = {
        f"awesome-{expected_version}.zip",
        "install.ps1",
        "install.sh",
    }
    expected_inventory = {*expected_assets, "SHA256SUMS"}
    try:
        if not stat.S_ISDIR(release.stat(follow_symlinks=False).st_mode):
            raise BundleVerificationError("release asset directory is invalid")
        entries = tuple(release.iterdir())
    except BundleVerificationError:
        raise
    except OSError as error:
        raise BundleVerificationError("release asset directory is invalid") from error
    if {entry.name for entry in entries} != expected_inventory or any(
        not _is_plain_file(entry) for entry in entries
    ):
        raise BundleVerificationError("release asset inventory is invalid")

    manifest = release / "SHA256SUMS"
    try:
        if manifest.stat().st_size > _MAX_CHECKSUM_MANIFEST_BYTES:
            raise BundleVerificationError("release checksum manifest is invalid")
        content = manifest.read_bytes()
        rendered = content.decode("ascii")
    except BundleVerificationError:
        raise
    except (OSError, UnicodeDecodeError) as error:
        raise BundleVerificationError("release checksum manifest is invalid") from error
    if not rendered or "\r" in rendered or not rendered.endswith("\n"):
        raise BundleVerificationError("release checksum manifest is invalid")

    observed: dict[str, str] = {}
    for line in rendered.splitlines():
        fields = line.split("  ")
        if len(fields) != 2:
            raise BundleVerificationError("release checksum manifest is invalid")
        digest, name = fields
        if (
            name in observed
            or name not in expected_assets
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise BundleVerificationError("release checksum manifest is invalid")
        observed[name] = digest
    if set(observed) != expected_assets:
        raise BundleVerificationError("release checksum manifest is incomplete")

    for name, expected_digest in observed.items():
        try:
            with (release / name).open("rb") as stream:
                actual_digest = hashlib.file_digest(stream, "sha256").hexdigest()
        except OSError as error:
            raise BundleVerificationError("release asset is unreadable") from error
        if actual_digest != expected_digest:
            raise BundleVerificationError("release asset checksum does not match")


def find_payload(archive: ZipFile, expected_version: str) -> str:
    prefix = f"awesome-{expected_version}/"
    infos = archive.infolist()
    names = tuple(info.filename for info in infos)
    required = {
        "VERSION",
        f"core/awesome_agent-{expected_version}-py3-none-any.whl",
        "core/requirements.lock",
        "tui/LICENSE",
        "tui/package-lock.json",
        "tui/package.json",
    }
    relatives = tuple(name.removeprefix(prefix) for name in names)
    if (
        not names
        or len(names) != len(set(names))
        or not required.issubset(relatives)
        or any(
            not relative.startswith("tui/dist/")
            for relative in set(relatives) - required
        )
        or any(
            not name.startswith(prefix)
            or "\\" in name
            or ":" in name
            or "\x00" in name
            or PurePosixPath(name).is_absolute()
            or any(part in {"", ".", ".."} for part in PurePosixPath(name).parts)
            for name in names
        )
        or any(
            info.is_dir()
            or (info.create_system == 3 and stat.S_ISLNK(info.external_attr >> 16))
            for info in infos
        )
    ):
        raise BundleVerificationError("bundle member inventory is invalid")
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
        storage_module.reset_local_state(lease)

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
        expected_origins = {
            version_module: import_root / "awesome_agent" / "version.py",
            storage_module: import_root / "awesome_agent" / "storage" / "__init__.py",
            paths_module: import_root / "awesome_agent" / "paths.py",
        }
        for module, expected_origin in expected_origins.items():
            module_file = getattr(module, "__file__", None)
            module_spec = getattr(module, "__spec__", None)
            spec_origin = getattr(module_spec, "origin", None)
            if not isinstance(module_file, str) or not isinstance(spec_origin, str):
                raise BundleVerificationError("wheel module origin is unavailable")
            if (
                Path(module_file).resolve() != expected_origin.resolve()
                or Path(spec_origin).resolve() != expected_origin.resolve()
            ):
                raise BundleVerificationError("wheel module escaped extraction root")
    except BundleVerificationError:
        raise
    except Exception as error:
        raise BundleVerificationError("wheel import failed") from error
    finally:
        sys.path.remove(str(import_root))
        for name in list(sys.modules):
            if name == "awesome_agent" or name.startswith("awesome_agent."):
                del sys.modules[name]
        sys.modules.update(previous_modules)
    return version_module, storage_module, paths_module


def _run_core_check(command: list[str], cwd: Path, diagnostic: str) -> None:
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as error:
        raise BundleVerificationError(diagnostic) from error
    if result.returncode != 0:
        raise BundleVerificationError(diagnostic)


def _verify_core_install(
    core: Path,
    wheel: Path,
    requirements: Path,
    expected_version: str,
) -> None:
    environment = core / ".verification-environment"
    uv = resolve_executable("uv")
    _run_core_check(
        [uv, "venv", "--python", sys.executable, str(environment)],
        core,
        "clean Core environment creation failed",
    )
    python = (
        environment / "Scripts" / "python.exe"
        if sys.platform == "win32"
        else environment / "bin" / "python"
    )
    _run_core_check(
        [
            uv,
            "pip",
            "install",
            "--python",
            str(python),
            "--require-hashes",
            "--no-deps",
            "--requirement",
            str(requirements),
        ],
        core,
        "locked Core dependency installation failed",
    )
    _run_core_check(
        [
            uv,
            "pip",
            "install",
            "--python",
            str(python),
            "--no-deps",
            str(wheel),
        ],
        core,
        "Core wheel installation failed",
    )
    _run_core_check(
        [uv, "pip", "check", "--python", str(python)],
        core,
        "Core dependency consistency check failed",
    )
    smoke = """
from importlib.metadata import version
from importlib.metadata import entry_points
from pathlib import Path
import sys
import awesome_agent
import awesome_agent.paths as paths
import awesome_agent.protocol.stdio as stdio
import awesome_agent.storage as storage
import awesome_agent.version as product

expected_version, environment = sys.argv[1:]
assert version("awesome-agent") == expected_version
assert awesome_agent.__version__ == expected_version
assert product.PRODUCT_VERSION == expected_version
root = Path(environment).resolve()
for module in (paths, stdio, storage, product):
    assert Path(module.__file__).resolve().is_relative_to(root)
scripts_by_name = {
    item.name: item.value for item in entry_points(group="console_scripts")
}
assert scripts_by_name["awesome-core"] == "awesome_agent.protocol.stdio:main"
assert scripts_by_name["awesome-dev"] == "awesome_agent.development.launcher:main"
scripts = Path(sys.executable).resolve().parent
entrypoints = ("awesome-core", "awesome-core.exe", "awesome-core.cmd")
assert any((scripts / name).is_file() for name in entrypoints)
"""
    _run_core_check(
        [
            str(python),
            "-I",
            "-c",
            smoke,
            expected_version,
            str(environment),
        ],
        core,
        "installed Core smoke check failed",
    )


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
    verify_release_assets(bundle.parent, expected_version)
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
        expected_wheel = f"awesome_agent-{expected_version}-py3-none-any.whl"
        if len(wheels) != 1 or wheels[0].name != expected_wheel:
            raise BundleVerificationError("bundle wheel identity is invalid")
        requirements = payload / "core" / "requirements.lock"
        try:
            if requirements.stat().st_size > MAX_RELEASE_REQUIREMENTS_BYTES:
                raise BundleVerificationError("bundle requirements are too large")
            validate_locked_requirements(requirements.read_bytes())
            validate_release_wheel(wheels[0], expected_version)
        except ReleaseContractError as error:
            subject = "requirements" if "requirement" in str(error) else "wheel"
            raise BundleVerificationError(
                f"bundle {subject} contract is invalid: {error}"
            ) from error
        except OSError as error:
            raise BundleVerificationError("bundle requirements are missing") from error
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

        _verify_core_install(
            payload / "core",
            wheels[0],
            requirements,
            expected_version,
        )

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
