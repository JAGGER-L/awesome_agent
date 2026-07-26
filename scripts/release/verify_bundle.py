from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
import shutil
import stat
import subprocess
import sys
import threading
import time
from collections.abc import Mapping, Sequence
from contextlib import suppress
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
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
_MAX_PROTOCOL_FRAME_BYTES = 1024 * 1024
_PROTOCOL_RESPONSE_TIMEOUT_SECONDS = 20.0
_CORE_EXIT_TIMEOUT_SECONDS = 10.0


def _reject_non_json_constant(value: str) -> None:
    del value
    raise ValueError("Non-finite numbers are not valid JSON.")


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
        "LICENSE",
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


def _protocol_request(
    process: subprocess.Popen[bytes],
    *,
    identifier: int,
    method: str,
    params: Mapping[str, object],
) -> None:
    stream = process.stdin
    if stream is None:
        raise BundleVerificationError("installed Core protocol input is unavailable")
    request = {
        "jsonrpc": "2.0",
        "id": identifier,
        "method": method,
        "params": dict(params),
    }
    try:
        stream.write(
            json.dumps(
                request,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )
        stream.flush()
    except (OSError, UnicodeEncodeError, ValueError) as error:
        raise BundleVerificationError(
            "installed Core protocol request failed"
        ) from error


def _protocol_response(
    frames: queue.Queue[bytes | None],
    *,
    identifier: int,
) -> Mapping[str, object]:
    deadline = time.monotonic() + _PROTOCOL_RESPONSE_TIMEOUT_SECONDS
    for _ in range(64):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise BundleVerificationError("installed Core protocol response timed out")
        try:
            raw = frames.get(timeout=remaining)
        except queue.Empty as error:
            raise BundleVerificationError(
                "installed Core protocol response timed out"
            ) from error
        if raw is None:
            raise BundleVerificationError("installed Core protocol closed early")
        if len(raw) > _MAX_PROTOCOL_FRAME_BYTES + 1 or not raw.endswith(b"\n"):
            raise BundleVerificationError("installed Core protocol frame is invalid")
        try:
            decoded = json.loads(raw, parse_constant=_reject_non_json_constant)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            raise BundleVerificationError(
                "installed Core protocol frame is invalid"
            ) from error
        if not isinstance(decoded, dict) or decoded.get("jsonrpc") != "2.0":
            raise BundleVerificationError("installed Core protocol frame is invalid")
        if decoded.get("method") == "event":
            continue
        if decoded.get("id") != identifier:
            raise BundleVerificationError("installed Core protocol response is invalid")
        return decoded
    raise BundleVerificationError("installed Core emitted too many unsolicited frames")


def _successful_protocol_value(
    response: Mapping[str, object],
) -> Mapping[str, object]:
    result = response.get("result")
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise BundleVerificationError("installed Core protocol request was rejected")
    value = result.get("value")
    if not isinstance(value, dict):
        raise BundleVerificationError("installed Core protocol response is invalid")
    return value


def _pump_protocol_frames(
    stream: object,
    frames: queue.Queue[bytes | None],
) -> None:
    reader = getattr(stream, "readline", None)
    if not callable(reader):
        frames.put(None)
        return
    try:
        while True:
            line = reader(_MAX_PROTOCOL_FRAME_BYTES + 2)
            if not isinstance(line, bytes) or not line:
                break
            frames.put(line)
    finally:
        frames.put(None)


def _stop_protocol_process(process: subprocess.Popen[bytes]) -> None:
    if process.stdin is not None:
        with suppress(OSError):
            process.stdin.close()
    if process.poll() is not None:
        return
    with suppress(OSError):
        process.terminate()
    try:
        process.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        with suppress(OSError):
            process.kill()
        with suppress(subprocess.TimeoutExpired):
            process.wait(timeout=2.0)


def _verify_core_protocol_handshake(
    command: Sequence[str],
    *,
    cwd: Path,
    home: Path,
    expected_version: str,
) -> None:
    cwd.mkdir(parents=True, exist_ok=False)
    home.mkdir(parents=True, exist_ok=False)
    excluded = {
        "DEEPSEEK_API_KEY",
        "MOONSHOT_API_KEY",
        "MEM0_API_KEY",
        "PYTHONHOME",
        "PYTHONPATH",
        "VIRTUAL_ENV",
    }
    environment = {
        name: value
        for name, value in os.environ.items()
        if name not in excluded and not name.startswith("AWESOME_")
    }
    environment["AWESOME_HOME"] = str(home)
    environment["PYTHONUTF8"] = "1"
    try:
        process = subprocess.Popen(
            list(command),
            cwd=cwd,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except OSError as error:
        raise BundleVerificationError(
            "installed Core protocol did not start"
        ) from error

    frames: queue.Queue[bytes | None] = queue.Queue()
    output = process.stdout
    if output is None:
        _stop_protocol_process(process)
        raise BundleVerificationError("installed Core protocol output is unavailable")
    reader = threading.Thread(
        target=_pump_protocol_frames,
        args=(output, frames),
        name="release-core-protocol-reader",
        daemon=True,
    )
    reader.start()
    completed = False
    try:
        _protocol_request(
            process,
            identifier=1,
            method="initialize",
            params={
                "protocol_version": 3,
                "client_name": "awesome",
                "client_version": expected_version,
            },
        )
        initialized = _successful_protocol_value(
            _protocol_response(frames, identifier=1)
        )
        if (
            initialized.get("protocol_version") != 3
            or initialized.get("product_version") != expected_version
            or initialized.get("status") != "trust_required"
        ):
            raise BundleVerificationError("installed Core protocol identity is invalid")
        interaction_id = initialized.get("interaction_id")
        if not isinstance(interaction_id, str) or not interaction_id:
            raise BundleVerificationError("installed Core trust interaction is invalid")

        _protocol_request(
            process,
            identifier=2,
            method="interaction.respond",
            params={"interaction_id": interaction_id, "decision": "trust"},
        )
        trusted = _successful_protocol_value(_protocol_response(frames, identifier=2))
        if trusted.get("accepted") is not True or trusted.get("status") != "resolved":
            raise BundleVerificationError(
                "installed Core trust interaction was not resolved"
            )

        _protocol_request(
            process,
            identifier=3,
            method="application.getState",
            params={},
        )
        state = _successful_protocol_value(_protocol_response(frames, identifier=3))
        if (
            state.get("initialized") is not True
            or state.get("workspace_trusted") is not True
        ):
            raise BundleVerificationError("installed Core state is not ready")

        _protocol_request(
            process,
            identifier=4,
            method="shutdown",
            params={},
        )
        stopped = _successful_protocol_value(_protocol_response(frames, identifier=4))
        if stopped.get("stopped") is not True:
            raise BundleVerificationError(
                "installed Core shutdown was not acknowledged"
            )
        if process.stdin is not None:
            with suppress(OSError):
                process.stdin.close()
        try:
            return_code = process.wait(timeout=_CORE_EXIT_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired as error:
            raise BundleVerificationError(
                "installed Core did not exit after shutdown"
            ) from error
        if return_code != 0:
            raise BundleVerificationError("installed Core exited unsuccessfully")
        completed = True
    finally:
        if not completed:
            _stop_protocol_process(process)
        reader.join(timeout=2.0)


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
scripts = root / ("Scripts" if sys.platform == "win32" else "bin")
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
    storage_contract = Path(__file__).with_name("storage_contract.py").resolve()
    _run_core_check(
        [
            str(python),
            "-I",
            str(storage_contract),
            expected_version,
            str(environment),
            str(core / ".storage-contract"),
        ],
        core,
        "installed Core storage contract failed",
    )
    scripts = _environment_scripts_directory(environment)
    entrypoint = scripts / (
        "awesome-core.exe" if sys.platform == "win32" else "awesome-core"
    )
    _verify_core_protocol_handshake(
        [str(entrypoint)],
        cwd=core / ".protocol-workspace",
        home=core / ".protocol-home",
        expected_version=expected_version,
    )


def _environment_scripts_directory(
    environment: Path,
    *,
    platform: str = sys.platform,
) -> Path:
    """Locate venv scripts from its root without following the Python symlink."""

    return environment / ("Scripts" if platform == "win32" else "bin")


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
            license_content = (payload / "LICENSE").read_bytes()
            if (payload / "tui" / "LICENSE").read_bytes() != license_content:
                raise BundleVerificationError("bundle license files do not match")
            validate_release_wheel(wheels[0], expected_version, license_content)
        except ReleaseContractError as error:
            subject = "requirements" if "requirement" in str(error) else "wheel"
            raise BundleVerificationError(
                f"bundle {subject} contract is invalid: {error}"
            ) from error
        except OSError as error:
            raise BundleVerificationError("bundle requirements are missing") from error
        allowed_migration_module = "awesome_agent/storage/migrations.py"
        with ZipFile(wheels[0]) as wheel_archive:
            module_paths = {
                PurePosixPath(name).as_posix()
                for name in wheel_archive.namelist()
                if name.endswith(".py")
            }
        forbidden_migration_modules = {
            path
            for path in module_paths
            if path != allowed_migration_module
            and (
                "/migrations/" in path.casefold()
                or PurePosixPath(path).stem.casefold() in {"migration", "migrations"}
            )
        }
        if forbidden_migration_modules:
            raise BundleVerificationError("wheel contains a migration module")

        _verify_core_install(
            payload / "core",
            wheels[0],
            requirements,
            expected_version,
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
