from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.release import verify_bundle as release_verifier
from scripts.release.build_bundle import (
    BundleError,
    assemble_bundle,
    read_version,
    validate_version_files,
)
from scripts.release.contracts import ReleaseContractError, validate_release_wheel
from scripts.release.verify_bundle import (
    BundleVerificationError,
    verify_release_assets,
    verify_release_bundle,
    verify_storage_contract,
)

from awesome_agent import paths as awesome_paths_module
from awesome_agent import storage as application_storage


def _write_test_wheel(
    path: Path,
    version: str,
    *,
    entry_points: bytes | None = None,
) -> None:
    dist_info = f"awesome_agent-{version}.dist-info"
    members = {
        "awesome_agent/__init__.py": b"",
        "awesome_agent/paths.py": b"",
        "awesome_agent/protocol/stdio.py": b"",
        "awesome_agent/storage/__init__.py": b"",
        "awesome_agent/version.py": f'PRODUCT_VERSION = "{version}"\n'.encode(),
        f"{dist_info}/METADATA": (
            f"Metadata-Version: 2.4\nName: awesome-agent\nVersion: {version}\n"
        ).encode(),
        f"{dist_info}/WHEEL": b"Wheel-Version: 1.0\n"
        b"Generator: awesome-tests\n"
        b"Root-Is-Purelib: true\n"
        b"Tag: py3-none-any\n",
        f"{dist_info}/entry_points.txt": entry_points
        or b"[console_scripts]\n"
        b"awesome-core = awesome_agent.protocol.stdio:main\n"
        b"awesome-dev = awesome_agent.development.launcher:main\n",
    }
    record_path = f"{dist_info}/RECORD"
    record = io.StringIO(newline="")
    writer = csv.writer(record, lineterminator="\n")
    for name, content in members.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=")
        writer.writerow((name, f"sha256={digest.decode('ascii')}", len(content)))
    writer.writerow((record_path, "", ""))
    members[record_path] = record.getvalue().encode("utf-8")

    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        for name, content in members.items():
            archive.writestr(name, content)


def _replace_bundle_member(bundle: Path, name: str, content: bytes) -> None:
    with ZipFile(bundle) as archive:
        members = {
            member: archive.read(member)
            for member in archive.namelist()
            if member != name
        }
    members[name] = content
    with ZipFile(bundle, "w", compression=ZIP_DEFLATED) as archive:
        for member, member_content in members.items():
            archive.writestr(member, member_content)


def _remove_bundle_member(bundle: Path, name: str) -> None:
    with ZipFile(bundle) as archive:
        members = {
            member: archive.read(member)
            for member in archive.namelist()
            if member != name
        }
    with ZipFile(bundle, "w", compression=ZIP_DEFLATED) as archive:
        for member, member_content in members.items():
            archive.writestr(member, member_content)


def _refresh_release_checksums(release: Path, version: str) -> None:
    names = (f"awesome-{version}.zip", "install.ps1", "install.sh")
    rendered = "".join(
        f"{hashlib.sha256((release / name).read_bytes()).hexdigest()}  {name}\n"
        for name in names
    )
    (release / "SHA256SUMS").write_bytes(rendered.encode("ascii"))


def _stub_runtime_verification(monkeypatch: pytest.MonkeyPatch) -> None:
    version_module = SimpleNamespace(PRODUCT_VERSION="1.0.0")
    monkeypatch.setattr(
        release_verifier,
        "_load_wheel_modules",
        lambda *_: (version_module, SimpleNamespace(), SimpleNamespace()),
    )
    monkeypatch.setattr(release_verifier, "verify_storage_contract", lambda *_: None)
    monkeypatch.setattr(release_verifier, "_verify_core_install", lambda *_: None)
    monkeypatch.setattr(release_verifier, "_verify_tui", lambda *_: None)


def _fixture(root: Path, *, version: str = "1.0.0") -> Path:
    (root / "VERSION").write_text(f"{version}\n", encoding="utf-8")
    (root / "install.sh").write_text(
        f'#!/bin/sh\nVERSION="{version}"\n',
        encoding="utf-8",
    )
    (root / "install.ps1").write_text(
        f'$Version = "{version}"\n',
        encoding="utf-8",
    )
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
    (root / "dist" / "release-requirements.txt").write_text(
        "\n".join(
            [
                "langgraph==1.2.6 \\",
                "    --hash=sha256:" + "a" * 64,
                "jsonschema==4.26.2 \\",
                "    --hash=sha256:" + "e" * 64,
                "mcp==1.28.1 \\",
                "    --hash=sha256:" + "b" * 64,
                "mem0ai==2.0.7 \\",
                "    --hash=sha256:" + "c" * 64,
                "openai==2.43.0 \\",
                "    --hash=sha256:" + "d" * 64,
                "",
            ]
        ),
        encoding="utf-8",
    )
    _write_test_wheel(wheel, version)
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

    _fixture(tmp_path)
    (tmp_path / "dist" / "release-requirements.txt").unlink()
    with pytest.raises(BundleError, match="requirements"):
        assemble_bundle(tmp_path, "1.0.0")


@pytest.mark.parametrize(
    "invalid_requirement",
    [
        "unbounded>=1.0 \\\n    --hash=sha256:" + "e" * 64,
        "direct @ https://example.invalid/direct.whl \\\n    --hash=sha256:" + "e" * 64,
        "git+https://example.invalid/repository.git@main",
        "../local-package.whl \\\n    --hash=sha256:" + "e" * 64,
        "--index-url https://example.invalid/simple",
        "--trusted-host example.invalid",
        "-r nested-requirements.txt",
        "hashless==1.0.0",
        "bad-hash==1.0.0 \\\n+    --hash=sha256:abcd",
    ],
)
def test_bundle_rejects_non_reproducible_lock_entries(
    tmp_path: Path,
    invalid_requirement: str,
) -> None:
    _fixture(tmp_path)
    requirements = tmp_path / "dist" / "release-requirements.txt"
    requirements.write_text(
        requirements.read_text(encoding="utf-8") + invalid_requirement + "\n",
        encoding="utf-8",
    )

    with pytest.raises(BundleError, match="requirements"):
        assemble_bundle(tmp_path, "1.0.0")


def test_bundle_verifier_rejects_empty_wheel_before_runtime_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fixture(tmp_path)
    bundle = assemble_bundle(tmp_path, "1.0.0").archive
    nested_wheel = io.BytesIO()
    with ZipFile(nested_wheel, "w"):
        pass
    wheel_name = "awesome-1.0.0/core/awesome_agent-1.0.0-py3-none-any.whl"
    _replace_bundle_member(bundle, wheel_name, nested_wheel.getvalue())
    _refresh_release_checksums(bundle.parent, "1.0.0")
    _stub_runtime_verification(monkeypatch)

    with pytest.raises(BundleVerificationError, match="wheel"):
        verify_release_bundle(bundle, "1.0.0")


def test_wheel_loader_rejects_editable_environment_fallback(tmp_path: Path) -> None:
    wheel = tmp_path / "awesome_agent-1.0.0-py3-none-any.whl"
    with ZipFile(wheel, "w"):
        pass

    with pytest.raises(BundleVerificationError, match="extraction root"):
        release_verifier._load_wheel_modules(wheel, tmp_path / "wheel-import")


@pytest.mark.parametrize(
    ("member", "replacement", "diagnostic"),
    [
        (
            "awesome_agent-1.0.0.dist-info/METADATA",
            b"Metadata-Version: 2.4\nName: forged\nVersion: 1.0.0\n",
            "identity",
        ),
        ("awesome_agent/version.py", b"PRODUCT_VERSION = '9.9.9'\n", "RECORD"),
    ],
)
def test_wheel_contract_rejects_metadata_and_record_tampering(
    tmp_path: Path,
    member: str,
    replacement: bytes,
    diagnostic: str,
) -> None:
    wheel = tmp_path / "awesome_agent-1.0.0-py3-none-any.whl"
    _write_test_wheel(wheel, "1.0.0")
    _replace_bundle_member(wheel, member, replacement)

    with pytest.raises(ReleaseContractError, match=diagnostic):
        validate_release_wheel(wheel, "1.0.0")


def test_wheel_contract_rejects_namespace_and_editable_wheels(tmp_path: Path) -> None:
    wheel = tmp_path / "awesome_agent-1.0.0-py3-none-any.whl"
    _write_test_wheel(wheel, "1.0.0")
    _remove_bundle_member(wheel, "awesome_agent/__init__.py")
    with pytest.raises(ReleaseContractError, match="required members"):
        validate_release_wheel(wheel, "1.0.0")

    _write_test_wheel(wheel, "1.0.0")
    _replace_bundle_member(wheel, "__editable__.awesome_agent.pth", b"fallback\n")
    with pytest.raises(ReleaseContractError, match="editable"):
        validate_release_wheel(wheel, "1.0.0")


def test_wheel_contract_rejects_forged_console_entry_points(tmp_path: Path) -> None:
    wheel = tmp_path / "awesome_agent-1.0.0-py3-none-any.whl"
    _write_test_wheel(
        wheel,
        "1.0.0",
        entry_points=(
            b"[console_scripts]\n"
            b"awesome-core = attacker.module:main\n"
            b"awesome-dev = awesome_agent.development.launcher:main\n"
        ),
    )

    with pytest.raises(ReleaseContractError, match="entry point"):
        validate_release_wheel(wheel, "1.0.0")


def test_bundle_verifier_rejects_archive_path_escape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fixture(tmp_path)
    bundle = assemble_bundle(tmp_path, "1.0.0").archive
    with ZipFile(bundle, "a", compression=ZIP_DEFLATED) as archive:
        archive.writestr("awesome-1.0.0/../../sentinel", b"outside")
    _refresh_release_checksums(bundle.parent, "1.0.0")
    _stub_runtime_verification(monkeypatch)

    with pytest.raises(BundleVerificationError, match="inventory"):
        verify_release_bundle(bundle, "1.0.0")


@pytest.mark.parametrize(
    "forged_lock",
    [
        b"openai>=2\n",
        b"openai @ https://example.invalid/openai.whl\n",
        b"--index-url https://example.invalid/simple\n",
    ],
)
def test_bundle_verifier_rejects_forged_dependency_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    forged_lock: bytes,
) -> None:
    _fixture(tmp_path)
    bundle = assemble_bundle(tmp_path, "1.0.0").archive
    _replace_bundle_member(
        bundle,
        "awesome-1.0.0/core/requirements.lock",
        forged_lock,
    )
    _refresh_release_checksums(bundle.parent, "1.0.0")
    _stub_runtime_verification(monkeypatch)

    with pytest.raises(BundleVerificationError, match="requirements"):
        verify_release_bundle(bundle, "1.0.0")


def test_core_install_verification_uses_hashes_isolation_and_dependency_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr(release_verifier, "resolve_executable", lambda _: "uv")
    monkeypatch.setattr(
        release_verifier,
        "_run_core_check",
        lambda command, *_: commands.append(command),
    )
    core = tmp_path / "core"
    core.mkdir()
    wheel = core / "awesome_agent-1.0.0-py3-none-any.whl"
    requirements = core / "requirements.lock"

    release_verifier._verify_core_install(
        core,
        wheel,
        requirements,
        "1.0.0",
    )

    assert len(commands) == 5
    assert commands[0][:4] == ["uv", "venv", "--python", sys.executable]
    assert "--require-hashes" in commands[1]
    assert "--no-deps" in commands[1]
    assert commands[1][-2:] == ["--requirement", str(requirements)]
    assert commands[2][-2:] == ["--no-deps", str(wheel)]
    assert commands[3][1:3] == ["pip", "check"]
    assert commands[4][1:3] == ["-I", "-c"]


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


def test_release_checksums_cover_and_verify_every_published_asset(
    tmp_path: Path,
) -> None:
    _fixture(tmp_path)
    result = assemble_bundle(tmp_path, "1.0.0")

    observed = {
        line.split("  ", maxsplit=1)[1]
        for line in result.checksums.read_text(encoding="ascii").splitlines()
    }
    assert observed == {"awesome-1.0.0.zip", "install.ps1", "install.sh"}
    verify_release_assets(result.archive.parent, "1.0.0")

    (result.archive.parent / "install.sh").write_bytes(b"tampered installer\n")
    with pytest.raises(BundleVerificationError, match="checksum"):
        verify_release_assets(result.archive.parent, "1.0.0")


@pytest.mark.parametrize("mutation", ["incomplete_manifest", "extra_asset"])
def test_release_checksum_contract_rejects_incomplete_or_ambiguous_inventory(
    tmp_path: Path,
    mutation: str,
) -> None:
    _fixture(tmp_path)
    result = assemble_bundle(tmp_path, "1.0.0")
    release = result.archive.parent
    if mutation == "incomplete_manifest":
        digest = hashlib.sha256(result.archive.read_bytes()).hexdigest()
        result.checksums.write_bytes(
            f"{digest}  {result.archive.name}\n".encode("ascii")
        )
    else:
        (release / "unexpected.txt").write_text("unexpected\n", encoding="utf-8")

    with pytest.raises(BundleVerificationError, match=r"(inventory|incomplete)"):
        verify_release_assets(release, "1.0.0")


def test_bundle_is_deterministic_and_has_exact_members(tmp_path: Path) -> None:
    _fixture(tmp_path)

    first = assemble_bundle(tmp_path, "1.0.0")
    first_bytes = first.archive.read_bytes()
    first_sums = first.checksums.read_text(encoding="utf-8")
    second = assemble_bundle(tmp_path, "1.0.0")

    assert second.archive.name == "awesome-1.0.0.zip"
    assert second.archive.read_bytes() == first_bytes
    assert second.checksums.read_text(encoding="utf-8") == first_sums
    assert [line.split("  ", maxsplit=1)[1] for line in first_sums.splitlines()] == [
        "awesome-1.0.0.zip",
        "install.ps1",
        "install.sh",
    ]
    assert {path.name for path in second.archive.parent.iterdir()} == {
        "install.sh",
        "install.ps1",
        "awesome-1.0.0.zip",
        "SHA256SUMS",
    }
    assert (second.archive.parent / "install.sh").read_bytes() == (
        tmp_path / "install.sh"
    ).read_bytes()
    assert (second.archive.parent / "install.ps1").read_bytes() == (
        tmp_path / "install.ps1"
    ).read_bytes()

    with ZipFile(second.archive) as archive:
        assert archive.namelist() == [
            "awesome-1.0.0/VERSION",
            "awesome-1.0.0/core/awesome_agent-1.0.0-py3-none-any.whl",
            "awesome-1.0.0/core/requirements.lock",
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
        constraints = archive.read("awesome-1.0.0/core/requirements.lock")
        assert b"langgraph==1.2.6" in constraints
        assert b"--hash=sha256:" in constraints


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("install.sh", '#!/bin/sh\nVERSION="9.9.9"\n'),
        ("install.ps1", '$Version = "9.9.9"\n'),
    ],
)
def test_bundle_rejects_installer_version_drift(
    tmp_path: Path,
    name: str,
    content: str,
) -> None:
    _fixture(tmp_path)
    (tmp_path / name).write_text(content, encoding="utf-8")

    with pytest.raises(BundleError, match="installer version"):
        assemble_bundle(tmp_path, "1.0.0")


def test_release_storage_contract_uses_current_schema_without_migrations(
    tmp_path: Path,
) -> None:
    contract_root = tmp_path / "storage-contract"
    verify_storage_contract(
        application_storage,
        awesome_paths_module,
        contract_root,
    )

    shutil.rmtree(contract_root)
    assert not contract_root.exists()
