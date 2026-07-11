from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+\n\Z")
_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
_FORBIDDEN_PARTS = {"__pycache__", "tests", ".env"}
_FORBIDDEN_SUFFIXES = {".map", ".pyc", ".pyo", ".ts", ".tsx"}


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


def _validate_wheel(path: Path, version: str) -> None:
    if not path.is_file():
        raise BundleError(f"required wheel is missing: {path.name}")
    metadata_suffix = f"awesome_agent-{version}.dist-info/METADATA"
    with ZipFile(path) as wheel:
        names = wheel.namelist()
        if metadata_suffix not in names:
            raise BundleError("wheel metadata version does not match VERSION")
        if any(_is_forbidden(PurePosixPath(name)) for name in names):
            raise BundleError("wheel contains forbidden development content")


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


def _write_member(archive: ZipFile, name: str, content: bytes) -> None:
    info = ZipInfo(name, date_time=_ZIP_TIME)
    info.compress_type = ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    archive.writestr(info, content, compress_type=ZIP_DEFLATED, compresslevel=9)


def assemble_bundle(root: Path, version: str) -> BundleResult:
    validate_version_files(root, version)
    wheel = root / "dist" / f"awesome_agent-{version}-py3-none-any.whl"
    _validate_wheel(wheel, version)
    tui_dist = _tui_dist_files(root)
    tui = root / "tui"
    required = (tui / "package.json", tui / "package-lock.json", tui / "LICENSE")
    if not all(path.is_file() for path in required):
        raise BundleError("required TUI package file is missing")

    release = root / "dist" / "release"
    if release.exists():
        shutil.rmtree(release)
    release.mkdir(parents=True)
    archive_path = release / f"awesome-{version}.zip"
    prefix = f"awesome-{version}"
    members: dict[str, bytes] = {
        f"{prefix}/VERSION": (root / "VERSION").read_bytes(),
        f"{prefix}/core/{wheel.name}": wheel.read_bytes(),
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

    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    checksums = release / "SHA256SUMS"
    checksums.write_text(f"{digest}  {archive_path.name}\n", encoding="ascii")
    return BundleResult(archive=archive_path, checksums=checksums)


def _run(root: Path, *command: str) -> None:
    executable = shutil.which(command[0])
    if executable is None:
        raise BundleError(f"release command is unavailable: {command[0]}")
    try:
        subprocess.run((executable, *command[1:]), cwd=root, check=True)
    except (OSError, subprocess.CalledProcessError) as error:
        raise BundleError(f"release command failed: {' '.join(command)}") from error


def build_bundle(root: Path) -> BundleResult:
    version = read_version(root)
    validate_version_files(root, version)
    _run(root, "node", "tui/scripts/sync-version.mjs", "--check")
    _run(root, "uv", "build", "--wheel")
    _run(root, "npm", "--prefix", "tui", "ci")
    _run(root, "npm", "--prefix", "tui", "run", "build")
    return assemble_bundle(root, version)


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    result = build_bundle(root)
    print(result.archive.relative_to(root).as_posix())
    print(result.checksums.relative_to(root).as_posix())


if __name__ == "__main__":
    main()
