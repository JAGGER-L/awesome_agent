import os
import subprocess
from pathlib import Path

import pytest

import awesome_agent.core.filesystem as core_filesystem_module
import awesome_agent.core.safe_files as safe_files_module
from awesome_agent.core.filesystem import (
    DirectoryPin,
)
from awesome_agent.core.filesystem import (
    FileIdentity as CoreFileIdentity,
)
from awesome_agent.extensions.skills import SkillSource, discover_skills


def _skill(root: Path, name: str, description: str, extra: str = "") -> None:
    directory = root / name
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n{extra}---\nbody secret",
        encoding="utf-8",
    )


def _skill_with_metadata(root: Path, name: str, metadata: str) -> None:
    directory = root / name
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        "description: metadata limits\n"
        "metadata:\n"
        f"{metadata}\n"
        "---\n"
        "body",
        encoding="utf-8",
    )


def test_discovery_uses_precedence_disable_and_trust(tmp_path: Path) -> None:
    bundled = tmp_path / "bundled"
    user = tmp_path / "user"
    workspace = tmp_path / "workspace" / ".agents" / "skills"
    plain = tmp_path / "workspace" / "skills"
    for root in (bundled, user, workspace, plain):
        root.mkdir(parents=True)
    _skill(bundled, "review", "bundled")
    _skill(user, "review", "user")
    _skill(workspace, "review", "workspace")
    _skill(plain, "ignored", "ignored")
    _skill(user, "disabled", "disabled")

    untrusted = discover_skills(
        bundled_root=bundled,
        user_root=user,
        workspace_root=workspace,
        workspace_trusted=False,
        disabled={"disabled"},
    )
    trusted = discover_skills(
        bundled_root=bundled,
        user_root=user,
        workspace_root=workspace,
        workspace_trusted=True,
        disabled={"disabled"},
    )

    assert untrusted.resolve("review").source is SkillSource.USER
    assert trusted.resolve("review").source is SkillSource.WORKSPACE
    assert "ignored" not in {item.name for item in trusted.descriptors()}
    assert "disabled" not in {item.name for item in trusted.descriptors()}
    assert any(item.code == "shadowed" for item in trusted.diagnostics())


def test_invalid_packages_become_diagnostics_not_global_failure(tmp_path: Path) -> None:
    user = tmp_path / "user"
    user.mkdir()
    _skill(user, "good", "valid", "allowed-tools: [read_file]\n")
    _skill(user, "unknown", "invalid", "unexpected-option: true\n")
    _skill(user, "mismatch", "invalid", "name: other\n")
    malformed = user / "broken"
    malformed.mkdir()
    (malformed / "SKILL.md").write_text("---\nname: [", encoding="utf-8")

    catalog = discover_skills(
        bundled_root=None,
        user_root=user,
        workspace_root=None,
        workspace_trusted=False,
    )

    assert [item.name for item in catalog.descriptors()] == ["good"]
    assert catalog.resolve("good").allowed_tools == ("read_file",)
    assert len(catalog.diagnostics()) == 3
    assert {item.code for item in catalog.diagnostics()} == {"invalid_skill"}


def test_identity_snapshots_are_ordered_bounded_and_path_opaque(
    tmp_path: Path,
) -> None:
    user = tmp_path / "user"
    user.mkdir()
    _skill(user, "zeta", "last")
    _skill(user, "alpha", "first", "allowed-tools: [read_file, mcp.demo.read]\n")

    catalog = discover_skills(
        bundled_root=None,
        user_root=user,
        workspace_root=None,
        workspace_trusted=False,
    )

    snapshots = catalog.identity_snapshots()
    assert tuple(item.name for item in snapshots) == ("alpha", "zeta")
    assert tuple(item.name for item in catalog.identity_snapshots(limit=1)) == (
        "alpha",
    )
    assert tuple(item.name for item in catalog.descriptors(limit=1)) == ("alpha",)
    assert snapshots[0] == catalog.identity_snapshot("alpha")
    assert snapshots[0].allowed_tools == ("read_file", "mcp.demo.read")
    assert snapshots[0].identity.startswith("skill-v1-sha256:")
    assert len(snapshots[0].identity) == len("skill-v1-sha256:") + 64
    assert str(tmp_path) not in snapshots[0].identity
    with pytest.raises(ValueError, match="non-negative"):
        catalog.identity_snapshots(limit=-1)


def test_skill_identity_changes_with_the_pinned_skill_file(tmp_path: Path) -> None:
    user = tmp_path / "user"
    user.mkdir()
    _skill(user, "review", "first")
    first = discover_skills(
        bundled_root=None,
        user_root=user,
        workspace_root=None,
        workspace_trusted=False,
    ).identity_snapshot("review")
    (user / "review" / "SKILL.md").write_text(
        "---\nname: review\ndescription: second\n---\nchanged body",
        encoding="utf-8",
    )
    second = discover_skills(
        bundled_root=None,
        user_root=user,
        workspace_root=None,
        workspace_trusted=False,
    ).identity_snapshot("review")

    assert first.identity != second.identity


@pytest.mark.parametrize(
    "allowed_tools",
    [
        "[read_file, read_file]",
        "['not a tool']",
        "[" + ", ".join(f"tool_{index}" for index in range(129)) + "]",
    ],
    ids=["duplicate", "invalid-name", "too-many"],
)
def test_invalid_allowed_tools_are_isolated_to_the_package(
    tmp_path: Path,
    allowed_tools: str,
) -> None:
    user = tmp_path / "user"
    user.mkdir()
    _skill(user, "good", "valid")
    _skill(user, "bad", "invalid", f"allowed-tools: {allowed_tools}\n")

    catalog = discover_skills(
        bundled_root=None,
        user_root=user,
        workspace_root=None,
        workspace_trusted=False,
    )

    assert [item.name for item in catalog.descriptors()] == ["good"]
    assert [(item.code, item.name) for item in catalog.diagnostics()] == [
        ("invalid_skill", "bad")
    ]


@pytest.mark.parametrize(
    "source",
    [SkillSource.BUNDLED, SkillSource.USER, SkillSource.WORKSPACE],
)
def test_deep_yaml_failure_is_isolated_to_one_skill_source(
    tmp_path: Path,
    source: SkillSource,
) -> None:
    workspace = tmp_path / "workspace"
    if source is SkillSource.WORKSPACE:
        root = workspace / ".awesome" / "skills"
    else:
        root = tmp_path / source.value
    _skill(root, "good", "valid")
    nested = "[" * 3_000 + "0" + "]" * 3_000
    _skill_with_metadata(root, "bad", f"  x: {nested}")

    catalog = discover_skills(
        bundled_root=root if source is SkillSource.BUNDLED else None,
        user_root=root if source is SkillSource.USER else None,
        workspace_root=root if source is SkillSource.WORKSPACE else None,
        workspace_anchor=workspace if source is SkillSource.WORKSPACE else None,
        workspace_trusted=source is SkillSource.WORKSPACE,
    )

    assert [item.name for item in catalog.descriptors()] == ["good"]
    assert [(item.code, item.name, item.source) for item in catalog.diagnostics()] == [
        ("invalid_skill", "bad", source)
    ]


@pytest.mark.parametrize(
    "metadata",
    [
        ("  base: &base [0]\n  refs: [" + ", ".join("*base" for _ in range(100)) + "]"),
        "  values: [" + ", ".join("0" for _ in range(5_000)) + "]",
        "  recursive: &self [*self]",
    ],
    ids=["aliases", "nodes", "recursive-alias"],
)
def test_workspace_yaml_resource_limits_keep_good_skill(
    tmp_path: Path,
    metadata: str,
) -> None:
    workspace = tmp_path / "workspace"
    root = workspace / ".awesome" / "skills"
    _skill(root, "good", "valid")
    _skill_with_metadata(root, "bad", metadata)

    catalog = discover_skills(
        bundled_root=None,
        user_root=None,
        workspace_root=root,
        workspace_anchor=workspace,
        workspace_trusted=True,
    )

    assert [item.name for item in catalog.descriptors()] == ["good"]
    assert [(item.code, item.name, item.source) for item in catalog.diagnostics()] == [
        ("invalid_skill", "bad", SkillSource.WORKSPACE)
    ]


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


def test_workspace_root_reparse_point_is_rejected_without_reading_target(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    awesome = workspace / ".awesome"
    outside = tmp_path / "outside"
    outside_skills = outside / "skills"
    awesome.mkdir(parents=True)
    outside_skills.mkdir(parents=True)
    _skill(outside_skills, "escaped", "must not be read")
    _directory_link(outside_skills, awesome / "skills")

    catalog = discover_skills(
        bundled_root=None,
        user_root=None,
        workspace_root=awesome / "skills",
        workspace_anchor=workspace,
        workspace_trusted=True,
    )

    assert catalog.descriptors() == ()
    assert [item.code for item in catalog.diagnostics()] == [
        "unsafe_workspace_skill_path"
    ]


def test_workspace_parent_reparse_point_is_rejected(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    outside_awesome = outside / ".awesome"
    outside_skills = outside_awesome / "skills"
    workspace.mkdir()
    outside_skills.mkdir(parents=True)
    _skill(outside_skills, "escaped", "must not be read")
    _directory_link(outside_awesome, workspace / ".awesome")

    catalog = discover_skills(
        bundled_root=None,
        user_root=None,
        workspace_root=workspace / ".awesome" / "skills",
        workspace_anchor=workspace,
        workspace_trusted=True,
    )

    assert catalog.descriptors() == ()
    assert [item.code for item in catalog.diagnostics()] == [
        "unsafe_workspace_skill_path"
    ]


def test_workspace_package_reparse_point_is_rejected_without_affecting_good_skill(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    root = workspace / ".awesome" / "skills"
    outside = tmp_path / "outside"
    root.mkdir(parents=True)
    outside.mkdir()
    _skill(root, "good", "valid")
    _skill(outside, "escaped", "must not be read")
    _directory_link(outside / "escaped", root / "escaped")

    catalog = discover_skills(
        bundled_root=None,
        user_root=None,
        workspace_root=root,
        workspace_anchor=workspace,
        workspace_trusted=True,
    )

    assert [item.name for item in catalog.descriptors()] == ["good"]
    assert any(
        item.code == "unsafe_workspace_skill_path" and item.name == "escaped"
        for item in catalog.diagnostics()
    )


def test_workspace_discovery_rejects_package_check_open_aba(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    root = workspace / ".awesome" / "skills"
    _skill(root, "good", "valid")
    _skill(root, "review", "trusted")
    package = root / "review"
    moved = root / "review.original"
    outside = tmp_path / "outside"
    _skill(outside, "review", "EXTERNAL-PACKAGE-ABA-SENTINEL")
    original_open = safe_files_module._open_pinned_directory
    attacked = False

    def open_during_package_aba(
        path: Path,
        *,
        parent: DirectoryPin | None = None,
        name: str | None = None,
        expected_identity: CoreFileIdentity | None = None,
        establish_mount_boundary: bool = False,
    ) -> DirectoryPin:
        nonlocal attacked
        if (
            not attacked
            and parent is not None
            and parent.path == root
            and name == "review"
        ):
            attacked = True
            package.rename(moved)
            _directory_link(outside / "review", package)
            try:
                return original_open(
                    path,
                    parent=parent,
                    name=name,
                    expected_identity=expected_identity,
                    establish_mount_boundary=establish_mount_boundary,
                )
            finally:
                _remove_directory_link(package)
                moved.rename(package)
        return original_open(
            path,
            parent=parent,
            name=name,
            expected_identity=expected_identity,
            establish_mount_boundary=establish_mount_boundary,
        )

    monkeypatch.setattr(
        safe_files_module,
        "_open_pinned_directory",
        open_during_package_aba,
    )

    catalog = discover_skills(
        bundled_root=None,
        user_root=None,
        workspace_root=root,
        workspace_anchor=workspace,
        workspace_trusted=True,
    )

    assert attacked is True
    assert [item.name for item in catalog.descriptors()] == ["good"]
    assert len(catalog.diagnostics()) == 1
    assert catalog.diagnostics()[0].name == "review"


def test_workspace_skill_md_is_bounded_during_discovery(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    root = workspace / ".awesome" / "skills"
    skill = root / "oversized"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_bytes(b"x" * (1024 * 1024 + 1))

    catalog = discover_skills(
        bundled_root=None,
        user_root=None,
        workspace_root=root,
        workspace_anchor=workspace,
        workspace_trusted=True,
    )

    assert catalog.descriptors() == ()
    assert [item.code for item in catalog.diagnostics()] == ["invalid_skill"]


@pytest.mark.parametrize(
    "data",
    [
        b"---\nname: bad\ndescription: binary\n---\nbody\x00hidden",
        b"---\nname: bad\ndescription: invalid utf8\n---\nbody\xff",
    ],
    ids=["binary", "invalid-utf8"],
)
def test_workspace_invalid_skill_text_isolated_from_good_package(
    tmp_path: Path,
    data: bytes,
) -> None:
    workspace = tmp_path / "workspace"
    root = workspace / ".awesome" / "skills"
    _skill(root, "good", "valid")
    bad = root / "bad"
    bad.mkdir()
    (bad / "SKILL.md").write_bytes(data)

    catalog = discover_skills(
        bundled_root=None,
        user_root=None,
        workspace_root=root,
        workspace_anchor=workspace,
        workspace_trusted=True,
    )

    assert [item.name for item in catalog.descriptors()] == ["good"]
    assert [(item.code, item.name) for item in catalog.diagnostics()] == [
        ("invalid_skill", "bad")
    ]


def test_user_skill_md_is_also_bounded_during_discovery(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    skill = root / "oversized"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_bytes(b"x" * (1024 * 1024 + 1))

    catalog = discover_skills(
        bundled_root=None,
        user_root=root,
        workspace_root=None,
        workspace_trusted=False,
    )

    assert catalog.descriptors() == ()
    assert [item.code for item in catalog.diagnostics()] == ["invalid_skill"]


def test_workspace_discovery_rejects_skill_file_replaced_before_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    root = workspace / ".awesome" / "skills"
    _skill(root, "good", "valid")
    _skill(root, "review", "trusted")
    skill_file = root / "review" / "SKILL.md"
    replacement = skill_file.with_suffix(".replacement")
    replacement.write_text(
        "---\nname: review\ndescription: replacement\n---\nreplacement body",
        encoding="utf-8",
    )
    original = skill_file.with_suffix(".original")

    real_lstat = core_filesystem_module.lstat_child
    replaced = False

    def replace_skill_between_lstat_and_open(
        parent: DirectoryPin,
        name: str,
    ) -> os.stat_result:
        nonlocal replaced
        result = real_lstat(parent, name)
        if not replaced and parent.path == skill_file.parent and name == "SKILL.md":
            replaced = True
            skill_file.rename(original)
            replacement.rename(skill_file)
        return result

    monkeypatch.setattr(
        core_filesystem_module,
        "lstat_child",
        replace_skill_between_lstat_and_open,
    )

    catalog = discover_skills(
        bundled_root=None,
        user_root=None,
        workspace_root=root,
        workspace_anchor=workspace,
        workspace_trusted=True,
    )

    assert replaced is True
    assert [item.name for item in catalog.descriptors()] == ["good"]
    assert [(item.code, item.name) for item in catalog.diagnostics()] == [
        ("invalid_skill", "review")
    ]


def test_workspace_discovery_rejects_in_place_skill_mutation_after_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    root = workspace / ".awesome" / "skills"
    _skill(root, "good", "valid")
    _skill(root, "review", "trusted")
    skill_file = root / "review" / "SKILL.md"
    original_identity = os.stat(skill_file)
    real_read = core_filesystem_module.read_descriptor
    mutated = False

    def mutate_skill_after_open(
        descriptor: int,
        *,
        max_bytes: int | None,
    ) -> bytes:
        nonlocal mutated
        opened = os.fstat(descriptor)
        if (
            not mutated
            and opened.st_dev == original_identity.st_dev
            and opened.st_ino == original_identity.st_ino
        ):
            mutated = True
            skill_file.write_text(
                "---\nname: review\ndescription: replacement\n---\nreplacement body",
                encoding="utf-8",
            )
        return real_read(descriptor, max_bytes=max_bytes)

    monkeypatch.setattr(
        core_filesystem_module,
        "read_descriptor",
        mutate_skill_after_open,
    )

    catalog = discover_skills(
        bundled_root=None,
        user_root=None,
        workspace_root=root,
        workspace_anchor=workspace,
        workspace_trusted=True,
    )

    assert mutated is True
    assert [item.name for item in catalog.descriptors()] == ["good"]
    assert [(item.code, item.name) for item in catalog.diagnostics()] == [
        ("invalid_skill", "review")
    ]
