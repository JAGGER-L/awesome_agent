import os
import subprocess
from pathlib import Path

import pytest

import awesome_agent.extensions.skills.discovery as discovery_module
from awesome_agent.context._safe_files import (
    BoundedFile,
    FileFingerprint,
    read_bounded_file,
)
from awesome_agent.extensions.skills import SkillSource, discover_skills


def _skill(root: Path, name: str, description: str, extra: str = "") -> None:
    directory = root / name
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n{extra}---\nbody secret",
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
    _skill(root, "review", "trusted")
    skill_file = root / "review" / "SKILL.md"
    replacement = skill_file.with_suffix(".replacement")
    replacement.write_text(
        "---\nname: review\ndescription: replacement\n---\nreplacement body",
        encoding="utf-8",
    )
    original = skill_file.with_suffix(".original")

    def replace_skill_before_open(
        path: Path,
        *,
        max_bytes: int,
        expected: FileFingerprint | None = None,
    ) -> BoundedFile:
        skill_file.rename(original)
        replacement.rename(skill_file)
        return read_bounded_file(path, max_bytes=max_bytes, expected=expected)

    monkeypatch.setattr(
        discovery_module,
        "read_bounded_file",
        replace_skill_before_open,
    )

    catalog = discover_skills(
        bundled_root=None,
        user_root=None,
        workspace_root=root,
        workspace_anchor=workspace,
        workspace_trusted=True,
    )

    assert catalog.descriptors() == ()
    assert [item.code for item in catalog.diagnostics()] == ["invalid_skill"]


def test_workspace_discovery_rejects_in_place_skill_mutation_after_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    root = workspace / ".awesome" / "skills"
    _skill(root, "review", "trusted")
    skill_file = root / "review" / "SKILL.md"

    def mutate_skill_before_open(
        path: Path,
        *,
        max_bytes: int,
        expected: FileFingerprint | None = None,
    ) -> BoundedFile:
        skill_file.write_text(
            "---\nname: review\ndescription: replacement\n---\nreplacement body",
            encoding="utf-8",
        )
        return read_bounded_file(path, max_bytes=max_bytes, expected=expected)

    monkeypatch.setattr(
        discovery_module,
        "read_bounded_file",
        mutate_skill_before_open,
    )

    catalog = discover_skills(
        bundled_root=None,
        user_root=None,
        workspace_root=root,
        workspace_anchor=workspace,
        workspace_trusted=True,
    )

    assert catalog.descriptors() == ()
    assert [item.code for item in catalog.diagnostics()] == ["invalid_skill"]
