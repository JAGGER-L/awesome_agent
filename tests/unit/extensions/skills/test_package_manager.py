import json
import os
import stat
import struct
import subprocess
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

import awesome_agent.extensions.skills.package_manager as package_manager_module
from awesome_agent.core.filesystem import (
    DirectoryPin,
    FileIdentity,
    MutationTargetChanged,
)
from awesome_agent.core.filesystem import (
    identity as core_file_identity,
)
from awesome_agent.core.filesystem import (
    remove_child as core_remove_child,
)
from awesome_agent.core.resource_lock import ResourceLockUnavailable
from awesome_agent.core.safe_files import PinnedPlainDirectory
from awesome_agent.extensions.skills import (
    SkillPackageAction,
    SkillPackageError,
    SkillPackageManager,
)


def _skill_directory(
    root: Path,
    *,
    name: str = "review",
    description: str = "Review code",
    body: str = "instructions",
) -> Path:
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        "allowed-tools: [read_file]\n"
        "---\n"
        f"{body}\n",
        encoding="utf-8",
    )
    (root / "guide.md").write_text("guide", encoding="utf-8")
    return root


def _manager(tmp_path: Path) -> SkillPackageManager:
    home = tmp_path / "home"
    return SkillPackageManager(home, home / "skills")


def _zip(
    path: Path,
    entries: tuple[tuple[str, bytes], ...],
) -> Path:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries:
            archive.writestr(name, data)
    raw = path.read_bytes()
    for name, _data in entries:
        if "\\" in name:
            raw = raw.replace(name.replace("\\", "/").encode(), name.encode())
    path.write_bytes(raw)
    return path


def _manifest(
    *,
    name: str = "review",
    description: str = "Review code",
    body: str = "instructions",
) -> bytes:
    return (
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        "allowed-tools: [read_file]\n"
        "---\n"
        f"{body}\n"
    ).encode()


def _serialized_identity(identity: FileIdentity) -> dict[str, int | bool]:
    return {
        "device": identity.device,
        "inode": identity.inode,
        "file_type": identity.file_type,
        "reparse": identity.reparse,
    }


def test_directory_install_list_and_quarantined_remove(tmp_path: Path) -> None:
    source = _skill_directory(tmp_path / "source")
    manager = _manager(tmp_path)

    installed = manager.install(source)
    source.joinpath("SKILL.md").write_text("changed source", encoding="utf-8")
    listed = manager.list()
    removed = manager.remove("review")

    assert installed.action is SkillPackageAction.INSTALLED
    assert installed.restart_required is True
    assert [(item.name, item.description, item.allowed_tools) for item in listed] == [
        ("review", "Review code", ("read_file",))
    ]
    assert removed.action is SkillPackageAction.REMOVED
    assert removed.restart_required is True
    assert manager.list() == ()
    assert (tmp_path / "home" / ".skills.lock").is_file()
    assert not (tmp_path / "home" / ".skills-transaction.json").exists()
    assert not any(
        item.name.startswith((".skill-stage-", ".skill-quarantine-"))
        for item in (tmp_path / "home" / "skills").iterdir()
    )


def test_zip_replace_removes_old_snapshot_and_duplicate_install_is_stable(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    manager.install(_skill_directory(tmp_path / "first", body="old body"))
    archive = _zip(
        tmp_path / "replacement.zip",
        (
            ("SKILL.md", _manifest(description="Updated", body="new body")),
            ("new.md", b"new resource"),
        ),
    )

    with pytest.raises(SkillPackageError) as duplicate:
        manager.install(archive)
    replaced = manager.install(archive, replace=True)

    assert duplicate.value.code == "package_exists"
    assert replaced.action is SkillPackageAction.REPLACED
    assert (tmp_path / "home" / "skills" / "review" / "new.md").read_text() == (
        "new resource"
    )
    assert not (tmp_path / "home" / "skills" / "review" / "guide.md").exists()
    assert [(item.name, item.description) for item in manager.list()] == [
        ("review", "Updated")
    ]


@pytest.mark.parametrize(
    "invalid_entries",
    [
        (("../escape", b"x"),),
        (("/absolute", b"x"),),
        (("C:/drive", b"x"),),
        (("//server/share", b"x"),),
        (("safe:ads", b"x"),),
        (("con.txt", b"x"),),
        (("trail.", b"x"),),
        (("trail ", b"x"),),
        (("folder\\item", b"x"),),
        (("folder//item", b"x"),),
        (("folder/./item", b"x"),),
        (("e\u0301.txt", b"x"),),
        (("A.txt", b"x"), ("a.txt", b"y")),
        (("node", b"x"), ("node/item", b"y")),
    ],
    ids=(
        "traversal",
        "absolute",
        "drive",
        "unc",
        "ads",
        "reserved",
        "trailing-dot",
        "trailing-space",
        "backslash",
        "repeated-separator",
        "dot-component",
        "non-nfc",
        "casefold-duplicate",
        "prefix-collision",
    ),
)
def test_zip_rejects_ambiguous_or_nonportable_paths(
    tmp_path: Path,
    invalid_entries: tuple[tuple[str, bytes], ...],
) -> None:
    archive = _zip(
        tmp_path / "invalid.zip",
        (("SKILL.md", _manifest()), *invalid_entries),
    )

    with pytest.raises(SkillPackageError) as raised:
        _manager(tmp_path).install(archive)

    assert raised.value.code == "invalid_package"
    assert str(tmp_path) not in raised.value.message


def test_zip_rejects_symlink_encryption_binary_manifest_and_bad_crc(
    tmp_path: Path,
) -> None:
    symlink_archive = tmp_path / "symlink.zip"
    with zipfile.ZipFile(symlink_archive, "w") as writer:
        writer.writestr("SKILL.md", _manifest())
        link = zipfile.ZipInfo("link.md")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        writer.writestr(link, b"outside")

    encrypted_archive = _zip(
        tmp_path / "encrypted.zip",
        (("SKILL.md", _manifest()),),
    )
    encrypted = bytearray(encrypted_archive.read_bytes())
    local = encrypted.index(b"PK\x03\x04")
    central = encrypted.index(b"PK\x01\x02")
    local_flags = struct.unpack_from("<H", encrypted, local + 6)[0] | 0x1
    central_flags = struct.unpack_from("<H", encrypted, central + 8)[0] | 0x1
    struct.pack_into("<H", encrypted, local + 6, local_flags)
    struct.pack_into("<H", encrypted, central + 8, central_flags)
    encrypted_archive.write_bytes(encrypted)

    binary_archive = _zip(
        tmp_path / "binary.zip",
        (("SKILL.md", _manifest() + b"\x00hidden"),),
    )
    corrupt_archive = _zip(
        tmp_path / "corrupt.zip",
        (("SKILL.md", _manifest()), ("guide.md", b"guide")),
    )
    corrupt = corrupt_archive.read_bytes()
    corrupt_archive.write_bytes(corrupt[:-8])

    for archive_path in (
        symlink_archive,
        encrypted_archive,
        binary_archive,
        corrupt_archive,
    ):
        with pytest.raises(SkillPackageError) as raised:
            _manager(tmp_path / archive_path.stem).install(archive_path)
        assert raised.value.code == "invalid_package"


def test_directory_rejects_hardlinks_symlinks_and_casefold_duplicates(
    tmp_path: Path,
) -> None:
    hardlink_package = _skill_directory(tmp_path / "hardlink")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    os.link(outside, hardlink_package / "hardlink.md")
    with pytest.raises(SkillPackageError) as hardlink:
        _manager(tmp_path / "hardlink-home").install(hardlink_package)
    assert hardlink.value.code == "invalid_package"

    symlink_package = _skill_directory(tmp_path / "symlink")
    try:
        os.symlink(outside, symlink_package / "symlink.md")
    except OSError:
        pass
    else:
        with pytest.raises(SkillPackageError) as symlink:
            _manager(tmp_path / "symlink-home").install(symlink_package)
        assert symlink.value.code == "invalid_package"

    if os.name != "nt":
        duplicate_package = _skill_directory(tmp_path / "duplicate")
        (duplicate_package / "A.txt").write_text("a", encoding="utf-8")
        (duplicate_package / "a.txt").write_text("b", encoding="utf-8")
        with pytest.raises(SkillPackageError) as duplicate:
            _manager(tmp_path / "duplicate-home").install(duplicate_package)
        assert duplicate.value.code == "invalid_package"


def test_package_limits_apply_to_zip_and_directory_snapshots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(package_manager_module, "_MAX_ENTRIES", 2)
    too_many = _zip(
        tmp_path / "too-many.zip",
        (("SKILL.md", _manifest()), ("one", b"1"), ("two", b"2")),
    )
    with pytest.raises(SkillPackageError) as entries:
        _manager(tmp_path / "entries-home").install(too_many)
    assert entries.value.code == "package_too_large"

    monkeypatch.setattr(package_manager_module, "_MAX_ENTRIES", 512)
    monkeypatch.setattr(package_manager_module, "_MAX_FILE_BYTES", 8)
    oversized = _zip(
        tmp_path / "oversized.zip",
        (("SKILL.md", _manifest()),),
    )
    with pytest.raises(SkillPackageError) as single:
        _manager(tmp_path / "single-home").install(oversized)
    assert single.value.code == "package_too_large"

    monkeypatch.setattr(package_manager_module, "_MAX_FILE_BYTES", 1024 * 1024)
    monkeypatch.setattr(package_manager_module, "_MAX_EXPANDED_BYTES", 200)
    total = _skill_directory(tmp_path / "total")
    (total / "large.txt").write_bytes(b"x" * 200)
    with pytest.raises(SkillPackageError) as expanded:
        _manager(tmp_path / "total-home").install(total)
    assert expanded.value.code == "package_too_large"


def test_zip_preflight_rejects_declared_entry_overflow_before_zipinfo_allocation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = _zip(
        tmp_path / "declared-overflow.zip",
        (("SKILL.md", _manifest()),),
    )
    raw = bytearray(archive.read_bytes())
    eocd = raw.rfind(b"PK\x05\x06")
    struct.pack_into("<H", raw, eocd + 8, 513)
    struct.pack_into("<H", raw, eocd + 10, 513)
    archive.write_bytes(raw)

    def forbid_zipfile(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("ZipFile must not allocate metadata before preflight")

    monkeypatch.setattr(zipfile, "ZipFile", forbid_zipfile)

    with pytest.raises(SkillPackageError) as raised:
        _manager(tmp_path).install(archive)

    assert raised.value.code == "package_too_large"


def test_zip_limits_count_implicit_parents_and_bound_depth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    implicit_parents = _zip(
        tmp_path / "implicit-parents.zip",
        (("SKILL.md", _manifest()), ("one/two/resource.md", b"resource")),
    )
    monkeypatch.setattr(package_manager_module, "_MAX_ENTRIES", 3)
    with pytest.raises(SkillPackageError) as entries:
        _manager(tmp_path / "entry-home").install(implicit_parents)
    assert entries.value.code == "package_too_large"

    monkeypatch.setattr(package_manager_module, "_MAX_ENTRIES", 512)
    deep_name = "/".join("level" for _ in range(65)) + "/resource.md"
    deep = _zip(
        tmp_path / "deep.zip",
        (("SKILL.md", _manifest()), (deep_name, b"resource")),
    )
    with pytest.raises(SkillPackageError) as depth:
        _manager(tmp_path / "depth-home").install(deep)
    assert depth.value.code == "package_too_large"


def test_zip_corruption_and_illegal_utf8_names_are_stable_invalid_packages(
    tmp_path: Path,
) -> None:
    damaged = _zip(
        tmp_path / "damaged-deflate.zip",
        (("SKILL.md", _manifest(body="compressible " * 100)),),
    )
    damaged_raw = bytearray(damaged.read_bytes())
    local = damaged_raw.index(b"PK\x03\x04")
    compressed_size = struct.unpack_from("<I", damaged_raw, local + 18)[0]
    name_length, extra_length = struct.unpack_from("<2H", damaged_raw, local + 26)
    payload = local + 30 + name_length + extra_length
    damaged_raw[payload + compressed_size // 2] ^= 0xFF
    damaged.write_bytes(damaged_raw)

    compressed: list[Path] = []
    for label, compression in (
        ("lzma", zipfile.ZIP_LZMA),
        ("bzip2", zipfile.ZIP_BZIP2),
    ):
        archive = tmp_path / f"damaged-{label}.zip"
        with zipfile.ZipFile(archive, "w", compression=compression) as writer:
            writer.writestr(
                "SKILL.md",
                _manifest(body="compressible " * 100),
            )
        raw = bytearray(archive.read_bytes())
        local = raw.index(b"PK\x03\x04")
        compressed_size = struct.unpack_from("<I", raw, local + 18)[0]
        name_length, extra_length = struct.unpack_from("<2H", raw, local + 26)
        payload = local + 30 + name_length + extra_length
        if compression == zipfile.ZIP_LZMA:
            raw[payload + 4] = 0xFF
        else:
            raw[payload + compressed_size // 2] ^= 0xFF
        archive.write_bytes(raw)
        compressed.append(archive)

    illegal_name = _zip(
        tmp_path / "illegal-utf8.zip",
        (("x.txt", b"content"),),
    )
    illegal_raw = bytearray(illegal_name.read_bytes())
    local = illegal_raw.index(b"PK\x03\x04")
    central = illegal_raw.index(b"PK\x01\x02")
    local_flags = struct.unpack_from("<H", illegal_raw, local + 6)[0] | 0x800
    central_flags = struct.unpack_from("<H", illegal_raw, central + 8)[0] | 0x800
    struct.pack_into("<H", illegal_raw, local + 6, local_flags)
    struct.pack_into("<H", illegal_raw, central + 8, central_flags)
    illegal_raw[local + 30] = 0xFF
    illegal_raw[central + 46] = 0xFF
    illegal_name.write_bytes(illegal_raw)

    for archive in (damaged, *compressed, illegal_name):
        with pytest.raises(SkillPackageError) as raised:
            _manager(tmp_path / archive.stem).install(archive)
        assert raised.value.code == "invalid_package"


def test_list_has_a_bounded_candidate_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(tmp_path)
    manager.list()
    skills_root = tmp_path / "home" / "skills"
    for name in ("one", "two", "three"):
        (skills_root / name).mkdir()
    monkeypatch.setattr(package_manager_module, "_MAX_ENTRIES", 2)

    with pytest.raises(SkillPackageError) as raised:
        manager.list()

    assert raised.value.code == "package_too_large"


def test_list_reads_only_each_root_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(tmp_path)
    source = _skill_directory(tmp_path / "source")
    resources = source / "resources"
    resources.mkdir()
    (resources / "large.bin").write_bytes(b"x" * (1024 * 1024))
    manager.install(source)

    def forbid_resource_listing(
        _directory: PinnedPlainDirectory,
    ) -> tuple[str, ...]:
        raise AssertionError("list() must not traverse Skill resource trees")

    monkeypatch.setattr(
        PinnedPlainDirectory,
        "names",
        forbid_resource_listing,
    )

    assert [item.name for item in manager.list()] == ["review"]


def test_replace_failure_rolls_back_old_package_and_cleans_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(tmp_path)
    manager.install(_skill_directory(tmp_path / "old", description="Old"))
    replacement = _skill_directory(tmp_path / "new", description="New")
    real_rename = package_manager_module._platform_rename_noreplace

    def fail_stage_publish(
        parent: DirectoryPin,
        source: str,
        destination: str,
    ) -> None:
        if source.startswith(".skill-stage-") and destination == "review":
            raise OSError("injected publish failure")
        real_rename(parent, source, destination)

    monkeypatch.setattr(
        package_manager_module,
        "_platform_rename_noreplace",
        fail_stage_publish,
    )
    with pytest.raises(SkillPackageError) as raised:
        manager.install(replacement, replace=True)

    assert raised.value.code == "transaction_failed"
    assert [(item.name, item.description) for item in manager.list()] == [
        ("review", "Old")
    ]
    assert not (tmp_path / "home" / ".skills-transaction.json").exists()


def test_replace_partial_quarantine_cleanup_rolls_forward(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(tmp_path)
    manager.install(_skill_directory(tmp_path / "old", description="Old"))
    replacement = _skill_directory(tmp_path / "new", description="New")
    real_remove_child = core_remove_child
    deleted = False

    def fail_after_one_delete(
        parent: DirectoryPin,
        name: str,
        *,
        directory: bool,
    ) -> None:
        nonlocal deleted
        inside_quarantine = any(
            part.startswith(".skill-quarantine-") for part in parent.path.parts
        )
        if inside_quarantine and not deleted:
            real_remove_child(parent, name, directory=directory)
            deleted = True
            raise OSError("injected partial quarantine cleanup failure")
        real_remove_child(parent, name, directory=directory)

    monkeypatch.setattr(package_manager_module, "remove_child", fail_after_one_delete)
    with pytest.raises(SkillPackageError) as raised:
        manager.install(replacement, replace=True)

    skills_root = tmp_path / "home" / "skills"
    assert raised.value.code == "transaction_failed"
    assert deleted is True
    assert "description: New" in (skills_root / "review" / "SKILL.md").read_text()
    assert any(
        item.name.startswith(".skill-quarantine-") for item in skills_root.iterdir()
    )
    assert (tmp_path / "home" / ".skills-transaction.json").is_file()

    monkeypatch.setattr(package_manager_module, "remove_child", real_remove_child)
    assert [(item.name, item.description) for item in manager.list()] == [
        ("review", "New")
    ]
    assert not any(
        item.name.startswith(".skill-quarantine-") for item in skills_root.iterdir()
    )
    assert not (tmp_path / "home" / ".skills-transaction.json").exists()


def test_published_marker_recovers_without_exposing_absolute_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(tmp_path)
    source = _skill_directory(tmp_path / "source")
    real_clear = manager._clear_marker

    def fail_clear(*, pins: package_manager_module._TransactionPins) -> None:
        del pins
        raise SkillPackageError("transaction_failed", "injected")

    monkeypatch.setattr(manager, "_clear_marker", fail_clear)
    with pytest.raises(SkillPackageError) as raised:
        manager.install(source)
    marker_path = tmp_path / "home" / ".skills-transaction.json"
    marker = marker_path.read_text(encoding="utf-8")
    assert raised.value.code == "transaction_failed"
    assert str(tmp_path) not in marker

    monkeypatch.setattr(manager, "_clear_marker", real_clear)
    assert [item.name for item in manager.list()] == ["review"]
    assert not marker_path.exists()


def test_published_replace_marker_never_runs_quarantined_recovery(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    manager.install(_skill_directory(tmp_path / "old", description="Old"))
    skills_root = tmp_path / "home" / "skills"
    target = skills_root / "review"
    original_identity = core_file_identity(os.lstat(target))
    stage_name = ".skill-stage-" + "a" * 32
    quarantine_name = ".skill-quarantine-" + "b" * 32
    stage = _skill_directory(
        skills_root / stage_name,
        description="Candidate",
    )
    stage_identity = core_file_identity(os.lstat(stage))
    os.rename(target, skills_root / quarantine_name)

    marker = {
        "version": 1,
        "action": "replace",
        "phase": "published",
        "name": "review",
        "stage_name": stage_name,
        "quarantine_name": quarantine_name,
        "stage_identity": _serialized_identity(stage_identity),
        "original_identity": _serialized_identity(original_identity),
    }
    marker_path = tmp_path / "home" / ".skills-transaction.json"
    marker_path.write_text(json.dumps(marker), encoding="utf-8")

    with pytest.raises(SkillPackageError) as raised:
        manager.list()

    assert raised.value.code == "transaction_failed"
    assert stage.is_dir()
    assert (skills_root / quarantine_name).is_dir()
    assert not target.exists()
    assert marker_path.exists()


def test_published_recovery_rejects_foreign_target_mount(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import awesome_agent.core.filesystem as filesystem

    manager = _manager(tmp_path)
    manager.install(_skill_directory(tmp_path / "old", description="Old"))
    skills_root = tmp_path / "home" / "skills"
    target = skills_root / "review"
    original_identity = core_file_identity(os.lstat(target))
    stage_name = ".skill-stage-" + "e" * 32
    quarantine_name = ".skill-quarantine-" + "f" * 32
    stage = _skill_directory(
        skills_root / stage_name,
        description="Candidate",
    )
    stage_identity = core_file_identity(os.lstat(stage))
    os.rename(target, skills_root / quarantine_name)
    os.rename(stage, target)
    marker_path = tmp_path / "home" / ".skills-transaction.json"
    marker_path.write_text(
        json.dumps(
            {
                "version": 1,
                "action": "replace",
                "phase": "published",
                "name": "review",
                "stage_name": stage_name,
                "quarantine_name": quarantine_name,
                "stage_identity": _serialized_identity(stage_identity),
                "original_identity": _serialized_identity(original_identity),
            }
        ),
        encoding="utf-8",
    )
    root_mount = filesystem.MountIdentity("device", 1)
    foreign_mount = filesystem.MountIdentity("device", 2)

    def path_mount(
        _path: Path,
        *,
        parent: DirectoryPin | None,
        name: str | None,
        status: os.stat_result,
    ) -> filesystem.MountIdentity:
        del parent, status
        return foreign_mount if name == "review" else root_mount

    monkeypatch.setattr(filesystem, "_path_mount_identity", path_mount)
    monkeypatch.setattr(
        filesystem,
        "_descriptor_mount_identity",
        lambda _descriptor, _status: root_mount,
    )

    with pytest.raises(SkillPackageError) as raised:
        manager.recover()

    assert raised.value.code == "transaction_failed"
    assert marker_path.is_file()
    assert target.is_dir()
    assert (skills_root / quarantine_name).is_dir()


def test_quarantined_replace_marker_rolls_back_before_commit(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    manager.install(_skill_directory(tmp_path / "old", description="Old"))
    skills_root = tmp_path / "home" / "skills"
    target = skills_root / "review"
    original_identity = core_file_identity(os.lstat(target))
    stage_name = ".skill-stage-" + "c" * 32
    quarantine_name = ".skill-quarantine-" + "d" * 32
    stage = _skill_directory(
        skills_root / stage_name,
        description="Candidate",
    )
    stage_identity = core_file_identity(os.lstat(stage))
    os.rename(target, skills_root / quarantine_name)
    os.rename(stage, target)
    marker = {
        "version": 1,
        "action": "replace",
        "phase": "quarantined",
        "name": "review",
        "stage_name": stage_name,
        "quarantine_name": quarantine_name,
        "stage_identity": _serialized_identity(stage_identity),
        "original_identity": _serialized_identity(original_identity),
    }
    marker_path = tmp_path / "home" / ".skills-transaction.json"
    marker_path.write_text(json.dumps(marker), encoding="utf-8")

    assert [(item.name, item.description) for item in manager.list()] == [
        ("review", "Old")
    ]
    assert not marker_path.exists()
    assert not any(
        item.name.startswith((".skill-stage-", ".skill-quarantine-"))
        for item in skills_root.iterdir()
    )


def test_remove_partial_quarantine_cleanup_rolls_forward(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(tmp_path)
    manager.install(_skill_directory(tmp_path / "source"))
    real_remove_child = core_remove_child
    deleted = False

    def fail_after_one_delete(
        parent: DirectoryPin,
        name: str,
        *,
        directory: bool,
    ) -> None:
        nonlocal deleted
        inside_quarantine = any(
            part.startswith(".skill-quarantine-") for part in parent.path.parts
        )
        if inside_quarantine and not deleted:
            real_remove_child(parent, name, directory=directory)
            deleted = True
            raise OSError("injected partial quarantine cleanup failure")
        real_remove_child(parent, name, directory=directory)

    monkeypatch.setattr(package_manager_module, "remove_child", fail_after_one_delete)
    with pytest.raises(SkillPackageError) as failed_remove:
        manager.remove("review")

    skills_root = tmp_path / "home" / "skills"
    assert failed_remove.value.code == "transaction_failed"
    assert deleted is True
    assert not (skills_root / "review").exists()
    assert any(
        item.name.startswith(".skill-quarantine-") for item in skills_root.iterdir()
    )

    monkeypatch.setattr(package_manager_module, "remove_child", real_remove_child)
    assert manager.list() == ()
    assert not any(
        item.name.startswith(".skill-quarantine-") for item in skills_root.iterdir()
    )
    assert not (tmp_path / "home" / ".skills-transaction.json").exists()


@pytest.mark.parametrize("operation", ["replace", "remove"])
def test_published_marker_fsync_failure_never_rolls_back_inline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    manager = _manager(tmp_path)
    manager.install(_skill_directory(tmp_path / "old", description="Old"))
    replacement = _skill_directory(tmp_path / "new", description="New")
    home = tmp_path / "home"
    skills_root = home / "skills"
    marker_path = home / ".skills-transaction.json"
    real_fsync = package_manager_module._fsync_directory
    injected = False

    def fail_after_published_marker_replace(path: Path) -> None:
        nonlocal injected
        if (
            not injected
            and path == home
            and marker_path.is_file()
            and json.loads(marker_path.read_text(encoding="utf-8"))["phase"]
            == "published"
        ):
            injected = True
            raise OSError("injected post-replace fsync failure")
        real_fsync(path)

    monkeypatch.setattr(
        package_manager_module,
        "_fsync_directory",
        fail_after_published_marker_replace,
    )
    with pytest.raises(SkillPackageError) as raised:
        if operation == "replace":
            manager.install(replacement, replace=True)
        else:
            manager.remove("review")

    assert raised.value.code == "transaction_failed"
    assert injected is True
    assert json.loads(marker_path.read_text(encoding="utf-8"))["phase"] == ("published")
    quarantines = tuple(
        path
        for path in skills_root.iterdir()
        if path.name.startswith(".skill-quarantine-")
    )
    assert len(quarantines) == 1
    if operation == "replace":
        assert "description: New" in (skills_root / "review" / "SKILL.md").read_text(
            encoding="utf-8"
        )
    else:
        assert not (skills_root / "review").exists()

    manager.recover()

    assert not marker_path.exists()
    assert not quarantines[0].exists()
    if operation == "replace":
        assert [(item.name, item.description) for item in manager.list()] == [
            ("review", "New")
        ]
    else:
        assert manager.list() == ()


def test_recover_removes_exact_orphan_stage_and_fixed_marker_temporary(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    manager.list()
    home = tmp_path / "home"
    stage = _skill_directory(home / "skills" / (".skill-stage-" + "a" * 32))
    marker_temporary = home / ".skills-transaction.tmp"
    marker_temporary.write_text("partial marker", encoding="utf-8")

    manager.recover()

    assert not stage.exists()
    assert not marker_temporary.exists()


def test_recovery_scan_reserves_internal_capacity_beyond_package_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(package_manager_module, "_MAX_ENTRIES", 2)
    manager = _manager(tmp_path)
    manager.install(_skill_directory(tmp_path / "one", name="one"))
    manager.install(_skill_directory(tmp_path / "two", name="two"))
    skills_root = tmp_path / "home" / "skills"
    orphan_one = _skill_directory(
        skills_root / (".skill-stage-" + "1" * 32),
    )

    manager.recover()

    assert not orphan_one.exists()
    assert [package.name for package in manager.list()] == ["one", "two"]

    orphan_two = _skill_directory(
        skills_root / (".skill-stage-" + "2" * 32),
    )
    orphan_three = _skill_directory(
        skills_root / (".skill-stage-" + "3" * 32),
    )
    with pytest.raises(SkillPackageError) as raised:
        manager.recover()

    assert raised.value.code == "transaction_failed"
    assert orphan_two.is_dir()
    assert orphan_three.is_dir()


def test_recover_preserves_unreferenced_quarantine_and_fails_closed(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    manager.list()
    quarantine = _skill_directory(
        tmp_path / "home" / "skills" / (".skill-quarantine-" + "b" * 32)
    )

    with pytest.raises(SkillPackageError) as raised:
        manager.recover()

    assert raised.value.code == "transaction_failed"
    assert quarantine.is_dir()


def test_orphan_stage_cleanup_is_bounded_before_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(tmp_path)
    manager.list()
    stage = _skill_directory(
        tmp_path / "home" / "skills" / (".skill-stage-" + "c" * 32)
    )
    (stage / "third.md").write_text("third", encoding="utf-8")
    monkeypatch.setattr(package_manager_module, "_MAX_ENTRIES", 2)

    with pytest.raises(SkillPackageError) as raised:
        manager.recover()

    assert raised.value.code == "transaction_failed"
    assert sorted(path.name for path in stage.iterdir()) == [
        "SKILL.md",
        "guide.md",
        "third.md",
    ]


def test_source_and_cleanup_reject_injected_mount_boundary_crossing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import awesome_agent.core.filesystem as filesystem

    manager = _manager(tmp_path)
    source = _skill_directory(tmp_path / "source")
    root_mount = filesystem.MountIdentity("device", 1)
    foreign_mount = filesystem.MountIdentity("device", 2)

    def path_mount(
        _path: Path,
        *,
        parent: DirectoryPin | None,
        name: str | None,
        status: os.stat_result,
    ) -> filesystem.MountIdentity:
        del parent, status
        if name == "guide.md":
            return foreign_mount
        return root_mount

    def descriptor_mount(
        _descriptor: int,
        _status: os.stat_result,
    ) -> filesystem.MountIdentity:
        return root_mount

    monkeypatch.setattr(filesystem, "_path_mount_identity", path_mount)
    monkeypatch.setattr(filesystem, "_descriptor_mount_identity", descriptor_mount)

    with pytest.raises(SkillPackageError) as source_error:
        manager.install(source)
    assert source_error.value.code == "invalid_package"

    manager.list()
    stage = _skill_directory(
        tmp_path / "home" / "skills" / (".skill-stage-" + "d" * 32)
    )

    def cleanup_path_mount(
        _path: Path,
        *,
        parent: DirectoryPin | None,
        name: str | None,
        status: os.stat_result,
    ) -> filesystem.MountIdentity:
        del parent, status
        if name == stage.name:
            return foreign_mount
        return root_mount

    monkeypatch.setattr(filesystem, "_path_mount_identity", cleanup_path_mount)
    with pytest.raises(SkillPackageError) as cleanup_error:
        manager.recover()
    assert cleanup_error.value.code == "transaction_failed"
    assert stage.is_dir()


def test_mount_change_after_open_has_stable_package_error_codes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import awesome_agent.core.filesystem as filesystem

    root_mount = filesystem.MountIdentity("device", 1)
    foreign_mount = filesystem.MountIdentity("device", 2)
    source = _skill_directory(tmp_path / "source")
    source_calls = 0

    def source_path_mount(
        path: Path,
        *,
        parent: DirectoryPin | None,
        name: str | None,
        status: os.stat_result,
    ) -> filesystem.MountIdentity:
        nonlocal source_calls
        del parent, name, status
        if path == source:
            source_calls += 1
            return root_mount if source_calls == 1 else foreign_mount
        return root_mount

    monkeypatch.setattr(filesystem, "_path_mount_identity", source_path_mount)
    monkeypatch.setattr(
        filesystem,
        "_descriptor_mount_identity",
        lambda _descriptor, _status: root_mount,
    )
    with pytest.raises(SkillPackageError) as source_error:
        _manager(tmp_path / "source-home").install(source)
    assert source_error.value.code == "invalid_package"

    monkeypatch.undo()
    transaction_manager = _manager(tmp_path / "transaction")
    transaction_manager.list()
    skills_root = tmp_path / "transaction" / "home" / "skills"
    skills_calls = 0

    def transaction_path_mount(
        path: Path,
        *,
        parent: DirectoryPin | None,
        name: str | None,
        status: os.stat_result,
    ) -> filesystem.MountIdentity:
        nonlocal skills_calls
        del parent, name, status
        if path == skills_root:
            skills_calls += 1
            return root_mount if skills_calls <= 2 else foreign_mount
        return root_mount

    monkeypatch.setattr(
        filesystem,
        "_path_mount_identity",
        transaction_path_mount,
    )
    monkeypatch.setattr(
        filesystem,
        "_descriptor_mount_identity",
        lambda _descriptor, _status: root_mount,
    )
    with pytest.raises(SkillPackageError) as transaction_error:
        transaction_manager.recover()
    assert transaction_error.value.code == "transaction_failed"


def test_package_lock_contention_has_stable_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(tmp_path)
    manager.list()

    @contextmanager
    def busy_lock(
        _path: Path,
        *,
        directory: DirectoryPin | None = None,
    ) -> Iterator[None]:
        del directory
        raise ResourceLockUnavailable
        yield

    monkeypatch.setattr(package_manager_module, "exclusive_resource_lock", busy_lock)
    with pytest.raises(SkillPackageError) as busy:
        manager.list()
    assert busy.value.code == "package_busy"


def test_invalid_source_and_remove_name_never_echo_paths(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    missing = tmp_path / "private" / "missing.zip"

    with pytest.raises(SkillPackageError) as source:
        manager.install(missing)
    with pytest.raises(SkillPackageError) as name:
        manager.remove("../escape")

    assert source.value.code == "invalid_source"
    assert name.value.code == "invalid_package"
    assert str(tmp_path) not in source.value.message
    assert str(tmp_path) not in name.value.message


def test_concurrent_install_target_is_never_overwritten(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(tmp_path)
    source = _skill_directory(tmp_path / "source")
    real_rename = package_manager_module._platform_rename_noreplace

    def race_target(
        parent: DirectoryPin,
        source_name: str,
        destination_name: str,
    ) -> None:
        if source_name.startswith(".skill-stage-") and destination_name == "review":
            competitor = parent.path / destination_name
            competitor.mkdir()
            (competitor / "sentinel.txt").write_text("competitor", encoding="utf-8")
        real_rename(parent, source_name, destination_name)

    monkeypatch.setattr(
        package_manager_module,
        "_platform_rename_noreplace",
        race_target,
    )
    with pytest.raises(SkillPackageError) as raised:
        manager.install(source)

    skills_root = tmp_path / "home" / "skills"
    assert raised.value.code == "package_exists"
    assert (skills_root / "review" / "sentinel.txt").read_text() == "competitor"
    assert not (tmp_path / "home" / ".skills-transaction.json").exists()
    assert not any(
        item.name.startswith(".skill-stage-") for item in skills_root.iterdir()
    )


def _directory_link(target: Path, link: Path) -> None:
    if os.name == "nt":
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            check=True,
            capture_output=True,
            text=True,
        )
        return
    os.symlink(target, link, target_is_directory=True)


def _remove_directory_link(link: Path) -> None:
    if os.name == "nt":
        link.rmdir()
    else:
        link.unlink()


def test_layout_rejects_linked_ancestor_without_mutating_external_target(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_text("outside", encoding="utf-8")
    linked_ancestor = tmp_path / "linked-ancestor"
    try:
        _directory_link(outside, linked_ancestor)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("directory links are unavailable")
    manager = SkillPackageManager(
        linked_ancestor / "home",
        linked_ancestor / "home" / "skills",
    )

    try:
        with pytest.raises(SkillPackageError) as raised:
            manager.list()

        assert raised.value.code == "transaction_failed"
        assert sentinel.read_text(encoding="utf-8") == "outside"
        assert tuple(item.name for item in outside.iterdir()) == ("sentinel.txt",)
    finally:
        _remove_directory_link(linked_ancestor)


def test_pinned_root_swap_cannot_redirect_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(tmp_path)
    manager.list()
    source = _skill_directory(tmp_path / "source")
    skills_root = tmp_path / "home" / "skills"
    moved_root = tmp_path / "home" / "skills-moved"
    redirect = tmp_path / "redirect"
    redirect.mkdir()
    (redirect / "sentinel.txt").write_text("outside", encoding="utf-8")
    real_rename = package_manager_module._platform_rename_noreplace
    swap_attempted = False

    def swap_root(
        parent: DirectoryPin,
        source_name: str,
        destination_name: str,
    ) -> None:
        nonlocal swap_attempted
        if not (
            source_name.startswith(".skill-stage-") and destination_name == "review"
        ):
            real_rename(parent, source_name, destination_name)
            return
        swap_attempted = True
        try:
            os.rename(skills_root, moved_root)
        except OSError as error:
            raise MutationTargetChanged("root swap was blocked") from error
        _directory_link(redirect, skills_root)
        try:
            parent.verify_reachable()
        finally:
            _remove_directory_link(skills_root)
            os.rename(moved_root, skills_root)

    monkeypatch.setattr(
        package_manager_module,
        "_platform_rename_noreplace",
        swap_root,
    )
    with pytest.raises(SkillPackageError) as raised:
        manager.install(source)

    assert swap_attempted is True
    assert raised.value.code == "transaction_failed"
    assert not (skills_root / "review").exists()
    assert tuple(item.name for item in redirect.iterdir()) == ("sentinel.txt",)


def test_pinned_package_directory_swap_cannot_redirect_file_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(tmp_path)
    source = _skill_directory(tmp_path / "source")
    resource = source / "resources"
    resource.mkdir()
    (resource / "nested.md").write_text("nested", encoding="utf-8")
    redirect = tmp_path / "redirect"
    redirect.mkdir()
    (redirect / "sentinel.txt").write_text("outside", encoding="utf-8")
    real_write = package_manager_module._write_new_file
    swap_attempted = False

    def swap_package_directory(parent: DirectoryPin, name: str, data: bytes) -> None:
        nonlocal swap_attempted
        if name != "nested.md":
            real_write(parent, name, data)
            return
        swap_attempted = True
        moved = parent.path.with_name(f"{parent.path.name}-moved")
        try:
            os.rename(parent.path, moved)
        except OSError as error:
            raise MutationTargetChanged("package swap was blocked") from error
        _directory_link(redirect, parent.path)
        try:
            real_write(parent, name, data)
        finally:
            _remove_directory_link(parent.path)
            os.rename(moved, parent.path)

    monkeypatch.setattr(
        package_manager_module, "_write_new_file", swap_package_directory
    )
    with pytest.raises(SkillPackageError) as raised:
        manager.install(source)

    installed = tmp_path / "home" / "skills" / "review"
    assert swap_attempted is True
    assert raised.value.code == "transaction_failed"
    assert not installed.exists()
    assert tuple(item.name for item in redirect.iterdir()) == ("sentinel.txt",)


def test_directory_package_rejects_junction_or_reparse_resource(tmp_path: Path) -> None:
    package = _skill_directory(tmp_path / "package")
    outside = tmp_path / "outside-directory"
    outside.mkdir()
    link = package / "linked-directory"
    try:
        _directory_link(outside, link)
    except OSError:
        pytest.skip("directory links are unavailable")
    try:
        with pytest.raises(SkillPackageError) as raised:
            _manager(tmp_path / "home-reparse").install(package)
        assert raised.value.code == "invalid_package"
    finally:
        _remove_directory_link(link)
