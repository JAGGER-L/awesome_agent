from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.release.compatibility_manifest import (
    CompatibilityManifestError,
    render_release_compatibility,
)
from scripts.release.contracts import (
    MAX_RELEASE_REQUIREMENTS_BYTES,
    ReleaseContractError,
    validate_locked_requirements,
    validate_release_wheel,
)

_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+\n\Z")
_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
_FORBIDDEN_PARTS = {"__pycache__", "tests", ".env"}
_FORBIDDEN_SUFFIXES = {".map", ".pyc", ".pyo", ".ts", ".tsx"}
_RELEASE_REQUIREMENTS = "release-requirements.txt"
_MIT_LICENSE_HEADER = "MIT License\n\n"
_MIT_LICENSE_COPYRIGHT = re.compile(r"Copyright \(c\) [^\r\n\x00]{1,200}")
_MIT_LICENSE_GRANT = (
    "Permission is hereby granted, free of charge, to any person obtaining a copy\n"
    'of this software and associated documentation files (the "Software"), to deal\n'
    "in the Software without restriction, including without limitation the rights\n"
    "to use, copy, modify, merge, publish, distribute, sublicense, and/or sell\n"
    "copies of the Software, and to permit persons to whom the Software is\n"
    "furnished to do so, subject to the following conditions:\n"
    "\n"
    "The above copyright notice and this permission notice shall be included in all\n"
    "copies or substantial portions of the Software.\n"
    "\n"
    'THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR\n'
    "IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,\n"
    "FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE\n"
    "AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER\n"
    "LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,\n"
    "OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE\n"
    "SOFTWARE.\n"
)


class BundleError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class BundleResult:
    archive: Path
    checksums: Path


def read_version(root: Path) -> str:
    raw = (root / "VERSION").read_text(encoding="utf-8")
    if _VERSION.fullmatch(raw) is None:
        raise BundleError("VERSION must contain MAJOR.MINOR.PATCH and one newline")
    return raw.removesuffix("\n")


def validate_version_files(root: Path, version: str) -> None:
    tui = root / "tui"
    try:
        package = json.loads((tui / "package.json").read_text(encoding="utf-8"))
        lock = json.loads((tui / "package-lock.json").read_text(encoding="utf-8"))
        source = (tui / "src" / "version.ts").read_text(encoding="utf-8")
    except (OSError, json.JSONDecodeError) as error:
        raise BundleError("TUI version files are missing or invalid") from error
    observed = {
        package.get("version"),
        lock.get("version"),
        lock.get("packages", {}).get("", {}).get("version"),
    }
    if observed != {version}:
        raise BundleError("TUI package version does not match VERSION")
    expected_source = f'export const PRODUCT_VERSION = "{version}" as const;\n'
    if source != expected_source:
        raise BundleError("TUI source version does not match VERSION")


def validate_license_files(root: Path) -> bytes:
    try:
        license_content = (root / "LICENSE").read_bytes()
        rendered_license = license_content.decode("utf-8")
        tui_license = (root / "tui" / "LICENSE").read_bytes()
        project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        package = json.loads(
            (root / "tui" / "package.json").read_text(encoding="utf-8")
        )
        lock = json.loads(
            (root / "tui" / "package-lock.json").read_text(encoding="utf-8")
        )
    except (
        OSError,
        UnicodeDecodeError,
        tomllib.TOMLDecodeError,
        json.JSONDecodeError,
    ) as error:
        raise BundleError("release license files are missing or invalid") from error

    if not rendered_license.startswith(_MIT_LICENSE_HEADER):
        raise BundleError("root license is not the canonical MIT grant")
    copyright_line, separator, grant = rendered_license.removeprefix(
        _MIT_LICENSE_HEADER
    ).partition("\n\n")
    if (
        separator != "\n\n"
        or _MIT_LICENSE_COPYRIGHT.fullmatch(copyright_line) is None
        or grant != _MIT_LICENSE_GRANT
    ):
        raise BundleError("root license is not the canonical MIT grant")
    if tui_license != license_content:
        raise BundleError("TUI license does not match the root license")

    project_metadata = project.get("project", {})
    if project_metadata.get("license") != "MIT" or project_metadata.get(
        "license-files"
    ) != ["LICENSE"]:
        raise BundleError("Python license metadata does not match the MIT license")
    if package.get("license") != "MIT":
        raise BundleError("TUI package license does not match the MIT license")
    if lock.get("packages", {}).get("", {}).get("license") != "MIT":
        raise BundleError("TUI lock license does not match the MIT license")
    return license_content


def _validate_wheel(path: Path, version: str, license_content: bytes) -> None:
    try:
        validate_release_wheel(path, version, license_content)
        with ZipFile(path) as wheel:
            if any(_is_forbidden(PurePosixPath(name)) for name in wheel.namelist()):
                raise BundleError("wheel contains forbidden development content")
    except ReleaseContractError as error:
        raise BundleError(f"release wheel is invalid: {error}") from error


def _is_forbidden(path: PurePosixPath) -> bool:
    lowered_parts = {part.casefold() for part in path.parts}
    return bool(lowered_parts & _FORBIDDEN_PARTS) or path.suffix.casefold() in (
        _FORBIDDEN_SUFFIXES
    )


def _tui_dist_files(root: Path) -> tuple[Path, ...]:
    dist = root / "tui" / "dist"
    entry = dist / "cli" / "index.js"
    if not entry.is_file():
        raise BundleError("compiled TUI entry is missing")
    files = tuple(sorted(path for path in dist.rglob("*") if path.is_file()))
    if not files:
        raise BundleError("compiled TUI output is missing")
    for path in files:
        relative = PurePosixPath(path.relative_to(dist).as_posix())
        if _is_forbidden(relative):
            raise BundleError(f"compiled TUI contains forbidden file: {relative}")
    return files


def validate_installer_files(root: Path, version: str) -> dict[str, bytes]:
    expected = {
        "install.sh": re.compile(
            rf'^VERSION="{re.escape(version)}"\r?$',
            re.MULTILINE,
        ),
        "install.ps1": re.compile(
            rf'^\$Version = "{re.escape(version)}"\r?$',
            re.MULTILINE,
        ),
    }
    assets: dict[str, bytes] = {}
    for name, pattern in expected.items():
        path = root / name
        try:
            content = path.read_bytes()
            rendered = content.decode("utf-8")
        except (OSError, UnicodeDecodeError) as error:
            raise BundleError(
                f"release installer is missing or invalid: {name}"
            ) from error
        if pattern.search(rendered) is None:
            raise BundleError(
                f"release installer version does not match VERSION: {name}"
            )
        assets[name] = content
    return assets


def _locked_requirements(root: Path) -> bytes:
    path = root / "dist" / _RELEASE_REQUIREMENTS
    try:
        if path.stat().st_size > MAX_RELEASE_REQUIREMENTS_BYTES:
            raise BundleError("locked release requirements are too large")
        content = path.read_bytes()
        validate_locked_requirements(content)
    except ReleaseContractError as error:
        raise BundleError(
            f"locked release requirements are invalid: {error}"
        ) from error
    except OSError as error:
        raise BundleError(
            "locked release requirements are missing or invalid"
        ) from error
    return content


def _write_member(archive: ZipFile, name: str, content: bytes) -> None:
    info = ZipInfo(name, date_time=_ZIP_TIME)
    info.compress_type = ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    archive.writestr(info, content, compress_type=ZIP_DEFLATED, compresslevel=9)


def assemble_bundle(root: Path, version: str) -> BundleResult:
    validate_version_files(root, version)
    license_content = validate_license_files(root)
    installers = validate_installer_files(root, version)
    wheel = root / "dist" / f"awesome_agent-{version}-py3-none-any.whl"
    _validate_wheel(wheel, version, license_content)
    requirements = _locked_requirements(root)
    try:
        compatibility = render_release_compatibility(root, version)
    except CompatibilityManifestError as error:
        raise BundleError("release compatibility manifest is invalid") from error
    tui_dist = _tui_dist_files(root)
    tui = root / "tui"
    required = (tui / "package.json", tui / "package-lock.json", tui / "LICENSE")
    if not all(path.is_file() for path in required):
        raise BundleError("required TUI package file is missing")

    release = root / "dist" / "release"
    if release.exists():
        shutil.rmtree(release)
    release.mkdir(parents=True)
    for name, content in installers.items():
        (release / name).write_bytes(content)
    archive_path = release / f"awesome-{version}.zip"
    prefix = f"awesome-{version}"
    members: dict[str, bytes] = {
        f"{prefix}/LICENSE": license_content,
        f"{prefix}/VERSION": (root / "VERSION").read_bytes(),
        f"{prefix}/compatibility.json": compatibility,
        f"{prefix}/core/{wheel.name}": wheel.read_bytes(),
        f"{prefix}/core/requirements.lock": requirements,
        f"{prefix}/tui/LICENSE": (tui / "LICENSE").read_bytes(),
        f"{prefix}/tui/package-lock.json": (tui / "package-lock.json").read_bytes(),
        f"{prefix}/tui/package.json": (tui / "package.json").read_bytes(),
    }
    for path in tui_dist:
        relative = path.relative_to(tui / "dist").as_posix()
        members[f"{prefix}/tui/dist/{relative}"] = path.read_bytes()

    with ZipFile(archive_path, "w") as archive:
        for name in sorted(members):
            _write_member(archive, name, members[name])

    checksums = release / "SHA256SUMS"
    published_assets = {
        **installers,
        archive_path.name: archive_path.read_bytes(),
    }
    checksum_lines = (
        f"{hashlib.sha256(content).hexdigest()}  {name}\n"
        for name, content in sorted(published_assets.items())
    )
    checksums.write_bytes("".join(checksum_lines).encode("ascii"))
    return BundleResult(archive=archive_path, checksums=checksums)


def source_date_epoch(root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "show", "-s", "--format=%ct", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as error:
        raise BundleError("release commit timestamp is unavailable") from error
    epoch = result.stdout.strip()
    if result.returncode != 0 or re.fullmatch(r"0|[1-9][0-9]{0,15}", epoch) is None:
        raise BundleError("release commit timestamp is unavailable")
    return epoch


def _run(
    root: Path,
    *command: str,
    environment: Mapping[str, str] | None = None,
) -> None:
    executable = shutil.which(command[0])
    if executable is None:
        raise BundleError(f"release command is unavailable: {command[0]}")
    try:
        subprocess.run(
            (executable, *command[1:]),
            cwd=root,
            env=environment,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise BundleError(f"release command failed: {' '.join(command)}") from error


def build_bundle(root: Path) -> BundleResult:
    version = read_version(root)
    validate_version_files(root, version)
    environment = os.environ.copy()
    environment["SOURCE_DATE_EPOCH"] = source_date_epoch(root)
    _run(
        root,
        "node",
        "tui/scripts/sync-version.mjs",
        "--check",
        environment=environment,
    )
    _run(
        root,
        "uv",
        "build",
        "--wheel",
        "--no-build-isolation",
        environment=environment,
    )
    _run(
        root,
        "uv",
        "export",
        "--quiet",
        "--locked",
        "--format",
        "requirements-txt",
        "--no-dev",
        "--extra",
        "memory",
        "--no-emit-project",
        "--output-file",
        f"dist/{_RELEASE_REQUIREMENTS}",
        environment=environment,
    )
    _run(
        root,
        "npm",
        "--prefix",
        "tui",
        "ci",
        environment=environment,
    )
    _run(
        root,
        "npm",
        "--prefix",
        "tui",
        "run",
        "build",
        environment=environment,
    )
    return assemble_bundle(root, version)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build the deterministic Awesome Agent release bundle."
    )
    parser.parse_args(argv)
    root = Path(__file__).resolve().parents[2]
    result = build_bundle(root)
    print(result.archive.relative_to(root).as_posix())
    print(result.checksums.relative_to(root).as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
