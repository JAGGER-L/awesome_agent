from __future__ import annotations

import base64
import configparser
import csv
import hashlib
import io
import re
import stat
from email.parser import BytesParser
from email.policy import default
from pathlib import Path, PurePosixPath
from zipfile import BadZipFile, ZipFile

MAX_RELEASE_REQUIREMENTS_BYTES = 4 * 1024 * 1024

_HASH = r"--hash=sha256:[0-9a-f]{64}"
_LOCKED_REQUIREMENT = re.compile(
    rf"^(?P<name>[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)"
    rf"==(?P<version>[A-Za-z0-9](?:[A-Za-z0-9._+!-]*[A-Za-z0-9])?)"
    rf"(?:\s*;\s*(?P<marker>.*?))?"
    rf"(?P<hashes>(?:\s+{_HASH})+)$"
)
_MARKER = re.compile(r"^[A-Za-z0-9_.'\"\s()!<>=~-]+$")
_REQUIRED_LOCKED_PROJECTS = {
    "jsonschema",
    "langgraph",
    "mcp",
    "mem0ai",
    "openai",
}


class ReleaseContractError(ValueError):
    """A release input is not reproducible or self-contained."""


class _CaseSensitiveConfigParser(configparser.ConfigParser):
    def optionxform(self, optionstr: str) -> str:
        return optionstr


def _normalized_project_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).casefold()


def _logical_requirement_lines(rendered: str) -> tuple[str, ...]:
    logical: list[str] = []
    pending: list[str] = []
    for physical in rendered.splitlines():
        stripped = physical.strip()
        if not stripped or stripped.startswith("#"):
            if pending:
                raise ReleaseContractError("release requirements are malformed")
            continue
        continued = stripped.endswith("\\")
        fragment = stripped[:-1].rstrip() if continued else stripped
        if not fragment:
            raise ReleaseContractError("release requirements are malformed")
        pending.append(fragment)
        if not continued:
            logical.append(" ".join(pending))
            pending.clear()
    if pending:
        raise ReleaseContractError("release requirements are malformed")
    return tuple(logical)


def validate_locked_requirements(content: bytes) -> frozenset[str]:
    if not content or len(content) > MAX_RELEASE_REQUIREMENTS_BYTES:
        raise ReleaseContractError("release requirements size is invalid")
    try:
        rendered = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ReleaseContractError(
            "release requirements encoding is invalid"
        ) from error
    if "\x00" in rendered or rendered.startswith("\ufeff"):
        raise ReleaseContractError("release requirements encoding is invalid")

    projects: set[str] = set()
    logical = _logical_requirement_lines(rendered)
    if not logical:
        raise ReleaseContractError("release requirements are empty")
    for requirement in logical:
        if (
            any(
                forbidden in requirement.casefold()
                for forbidden in ("://", "git+", "file:", "--index", "--trusted-host")
            )
            or "@" in requirement
        ):
            raise ReleaseContractError("release requirements contain external sources")
        matched = _LOCKED_REQUIREMENT.fullmatch(requirement)
        if matched is None:
            raise ReleaseContractError(
                "release requirements must use exact versions and SHA-256 hashes"
            )
        marker = matched.group("marker")
        if marker is not None and (
            not marker.strip() or _MARKER.fullmatch(marker) is None
        ):
            raise ReleaseContractError("release requirement marker is invalid")
        projects.add(_normalized_project_name(matched.group("name")))

    if not _REQUIRED_LOCKED_PROJECTS.issubset(projects):
        raise ReleaseContractError("release requirements are incomplete")
    return frozenset(projects)


def _safe_archive_name(name: str) -> bool:
    path = PurePosixPath(name)
    return bool(
        name
        and "\\" not in name
        and ":" not in name
        and "\x00" not in name
        and not path.is_absolute()
        and all(part not in {"", ".", ".."} for part in path.parts)
    )


def _parse_metadata(content: bytes, expected_version: str) -> None:
    metadata = BytesParser(policy=default).parsebytes(content)
    names = metadata.get_all("Name", [])
    versions = metadata.get_all("Version", [])
    if len(names) != 1 or _normalized_project_name(names[0]) != "awesome-agent":
        raise ReleaseContractError("wheel project identity is invalid")
    if versions != [expected_version]:
        raise ReleaseContractError("wheel metadata version is invalid")


def _parse_wheel_metadata(content: bytes) -> None:
    metadata = BytesParser(policy=default).parsebytes(content)
    if metadata.get_all("Wheel-Version", []) != ["1.0"]:
        raise ReleaseContractError("wheel format version is invalid")
    if metadata.get_all("Root-Is-Purelib", []) != ["true"]:
        raise ReleaseContractError("wheel purelib contract is invalid")
    if "py3-none-any" not in metadata.get_all("Tag", []):
        raise ReleaseContractError("wheel compatibility tag is invalid")


def _parse_entry_points(content: bytes) -> None:
    try:
        rendered = content.decode("utf-8")
        parser = _CaseSensitiveConfigParser(interpolation=None, strict=True)
        parser.read_string(rendered)
    except (UnicodeDecodeError, configparser.Error) as error:
        raise ReleaseContractError("wheel entry point metadata is invalid") from error
    expected = {
        "awesome-core": "awesome_agent.protocol.stdio:main",
        "awesome-dev": "awesome_agent.development.launcher:main",
    }
    if (
        parser.sections() != ["console_scripts"]
        or dict(parser.items("console_scripts")) != expected
    ):
        raise ReleaseContractError("wheel entry point contract is invalid")


def _validate_record(
    archive: ZipFile,
    names: tuple[str, ...],
    record_path: str,
) -> None:
    try:
        rendered = archive.read(record_path).decode("utf-8")
        rows = tuple(csv.reader(io.StringIO(rendered), strict=True))
    except (UnicodeDecodeError, csv.Error, KeyError) as error:
        raise ReleaseContractError("wheel RECORD is invalid") from error

    observed: dict[str, tuple[str, str]] = {}
    for row in rows:
        if len(row) != 3 or not _safe_archive_name(row[0]) or row[0] in observed:
            raise ReleaseContractError("wheel RECORD is invalid")
        observed[row[0]] = (row[1], row[2])
    if set(observed) != set(names):
        raise ReleaseContractError("wheel RECORD inventory is invalid")

    for name in names:
        digest, size = observed[name]
        if name == record_path:
            if digest or size:
                raise ReleaseContractError("wheel RECORD self-entry is invalid")
            continue
        content = archive.read(name)
        expected_digest = base64.urlsafe_b64encode(
            hashlib.sha256(content).digest()
        ).rstrip(b"=")
        if digest != f"sha256={expected_digest.decode('ascii')}" or size != str(
            len(content)
        ):
            raise ReleaseContractError("wheel RECORD digest is invalid")


def validate_release_wheel(path: Path, expected_version: str) -> None:
    expected_name = f"awesome_agent-{expected_version}-py3-none-any.whl"
    if not path.is_file() or path.name != expected_name:
        raise ReleaseContractError("wheel filename is invalid")
    dist_info = f"awesome_agent-{expected_version}.dist-info"
    required = {
        "awesome_agent/__init__.py",
        "awesome_agent/paths.py",
        "awesome_agent/protocol/stdio.py",
        "awesome_agent/storage/__init__.py",
        "awesome_agent/version.py",
        f"{dist_info}/METADATA",
        f"{dist_info}/RECORD",
        f"{dist_info}/WHEEL",
        f"{dist_info}/entry_points.txt",
    }
    try:
        with ZipFile(path) as archive:
            infos = archive.infolist()
            names = tuple(info.filename for info in infos)
            if (
                len(names) != len(set(names))
                or any(
                    info.is_dir() or not _safe_archive_name(info.filename)
                    for info in infos
                )
                or any(
                    stat.S_ISLNK(info.external_attr >> 16)
                    for info in infos
                    if info.create_system == 3
                )
            ):
                raise ReleaseContractError("wheel member inventory is invalid")
            if not required.issubset(names):
                raise ReleaseContractError("wheel required members are incomplete")
            if any(
                name.casefold().endswith((".pth", ".egg-link"))
                or PurePosixPath(name).name.casefold().startswith("__editable__")
                for name in names
            ):
                raise ReleaseContractError("editable wheel content is forbidden")
            _parse_metadata(archive.read(f"{dist_info}/METADATA"), expected_version)
            _parse_wheel_metadata(archive.read(f"{dist_info}/WHEEL"))
            _parse_entry_points(archive.read(f"{dist_info}/entry_points.txt"))
            _validate_record(archive, names, f"{dist_info}/RECORD")
    except ReleaseContractError:
        raise
    except (BadZipFile, OSError, KeyError) as error:
        raise ReleaseContractError("wheel archive is invalid") from error
