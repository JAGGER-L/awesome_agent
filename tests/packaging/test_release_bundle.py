from __future__ import annotations

import json
import sys
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.release.build_bundle import (
    BundleError,
    assemble_bundle,
    read_version,
    validate_version_files,
)


def _fixture(root: Path, *, version: str = "1.0.0") -> Path:
    (root / "VERSION").write_text(f"{version}\n", encoding="utf-8")
    tui = root / "tui"
    (tui / "dist" / "cli").mkdir(parents=True, exist_ok=True)
    (tui / "dist" / "cli" / "index.js").write_text(
        "#!/usr/bin/env node\n",
        encoding="utf-8",
    )
    package = {"name": "@awesome-agent/tui", "version": version}
    lock = {"name": "@awesome-agent/tui", "version": version, "packages": {"": package}}
    (tui / "package.json").write_text(
        f"{json.dumps(package, indent=2)}\n",
        encoding="utf-8",
    )
    (tui / "package-lock.json").write_text(
        f"{json.dumps(lock, indent=2)}\n",
        encoding="utf-8",
    )
    (tui / "LICENSE").write_text("test license\n", encoding="utf-8")
    source = tui / "src"
    source.mkdir(exist_ok=True)
    (source / "version.ts").write_text(
        f'export const PRODUCT_VERSION = "{version}" as const;\n',
        encoding="utf-8",
    )
    wheel = root / "dist" / f"awesome_agent-{version}-py3-none-any.whl"
    wheel.parent.mkdir(exist_ok=True)
    with ZipFile(wheel, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("awesome_agent/__init__.py", "")
        archive.writestr(
            f"awesome_agent-{version}.dist-info/METADATA",
            f"Version: {version}\n",
        )
    return wheel


def test_version_requires_exact_semver_and_final_newline(tmp_path: Path) -> None:
    version_file = tmp_path / "VERSION"
    for content in ("1.0.0", "v1.0.0\n", "1.0\n", "1.0.0\n\n"):
        version_file.write_text(content, encoding="utf-8")
        with pytest.raises(BundleError):
            read_version(tmp_path)

    version_file.write_text("1.0.0\n", encoding="utf-8")
    assert read_version(tmp_path) == "1.0.0"


def test_version_files_must_not_drift(tmp_path: Path) -> None:
    _fixture(tmp_path)
    validate_version_files(tmp_path, "1.0.0")

    package = tmp_path / "tui" / "package.json"
    package.write_text('{"version":"2.0.0"}\n', encoding="utf-8")
    with pytest.raises(BundleError, match="version"):
        validate_version_files(tmp_path, "1.0.0")


def test_bundle_requires_wheel_and_compiled_tui(tmp_path: Path) -> None:
    wheel = _fixture(tmp_path)
    wheel.unlink()
    with pytest.raises(BundleError, match="wheel"):
        assemble_bundle(tmp_path, "1.0.0")

    wheel = _fixture(tmp_path)
    (tmp_path / "tui" / "dist" / "cli" / "index.js").unlink()
    with pytest.raises(BundleError, match="TUI"):
        assemble_bundle(tmp_path, "1.0.0")


@pytest.mark.parametrize(
    "relative",
    ["debug.js.map", "tests/leak.js", "__pycache__/leak.pyc", ".env"],
)
def test_bundle_rejects_nonproduction_tui_files(
    tmp_path: Path,
    relative: str,
) -> None:
    _fixture(tmp_path)
    forbidden = tmp_path / "tui" / "dist" / relative
    forbidden.parent.mkdir(parents=True, exist_ok=True)
    forbidden.write_text("forbidden", encoding="utf-8")

    with pytest.raises(BundleError, match="forbidden"):
        assemble_bundle(tmp_path, "1.0.0")


def test_bundle_is_deterministic_and_has_exact_members(tmp_path: Path) -> None:
    _fixture(tmp_path)

    first = assemble_bundle(tmp_path, "1.0.0")
    first_bytes = first.archive.read_bytes()
    first_sums = first.checksums.read_text(encoding="utf-8")
    second = assemble_bundle(tmp_path, "1.0.0")

    assert second.archive.name == "awesome-1.0.0.zip"
    assert second.archive.read_bytes() == first_bytes
    assert second.checksums.read_text(encoding="utf-8") == first_sums
    assert first_sums.endswith("  awesome-1.0.0.zip\n")

    with ZipFile(second.archive) as archive:
        assert archive.namelist() == [
            "awesome-1.0.0/VERSION",
            "awesome-1.0.0/core/awesome_agent-1.0.0-py3-none-any.whl",
            "awesome-1.0.0/tui/LICENSE",
            "awesome-1.0.0/tui/dist/cli/index.js",
            "awesome-1.0.0/tui/package-lock.json",
            "awesome-1.0.0/tui/package.json",
        ]
        assert {entry.date_time for entry in archive.infolist()} == {
            (1980, 1, 1, 0, 0, 0)
        }
        wheel_name = "awesome-1.0.0/core/awesome_agent-1.0.0-py3-none-any.whl"
        with archive.open(wheel_name) as wheel_stream:
            assert wheel_stream.read(2) == b"PK"
