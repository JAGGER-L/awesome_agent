import os
import subprocess
from pathlib import Path

import pytest

import awesome_agent.extensions.skills.loader as loader_module
from awesome_agent.context._safe_files import (
    BoundedFile,
    FileFingerprint,
    read_bounded_file,
)
from awesome_agent.extensions.skills import (
    SkillCatalog,
    SkillLoader,
    SkillResourceError,
    discover_skills,
)


def _catalog(tmp_path: Path) -> SkillCatalog:
    root = tmp_path / "skills"
    skill = root / "review"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: review\ndescription: Review code\nallowed-tools: [execute]\n---\n"
        + "instruction\n" * 10_000,
        encoding="utf-8",
    )
    (skill / "guide.md").write_text("guide\n" * 100, encoding="utf-8")
    return discover_skills(
        bundled_root=None,
        user_root=root,
        workspace_root=None,
        workspace_trusted=False,
    )


def _workspace_catalog(tmp_path: Path) -> SkillCatalog:
    workspace = tmp_path / "workspace"
    root = workspace / ".awesome" / "skills"
    skill = root / "review"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: review\ndescription: Review code\n---\ninstruction\n",
        encoding="utf-8",
    )
    (skill / "guide.md").write_text("safe guide", encoding="utf-8")
    return discover_skills(
        bundled_root=None,
        user_root=None,
        workspace_root=root,
        workspace_anchor=workspace,
        workspace_trusted=True,
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


def test_loader_is_lazy_bounded_and_allowed_tools_are_diagnostic(
    tmp_path: Path,
) -> None:
    loader = SkillLoader(_catalog(tmp_path))

    loaded = loader.load("review", token_limit=5_000)
    resource = loader.read_resource("review", "guide.md", token_limit=10)

    assert loaded.truncated is True
    assert loaded.estimated_tokens <= 5_000
    assert loaded.descriptor.allowed_tools == ("execute",)
    assert resource.truncated is True
    assert resource.estimated_tokens <= 10


def test_resource_rejects_escape_binary_symlink_and_missing(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)
    loader = SkillLoader(catalog)
    root = catalog.resolve("review").root
    (root / "binary.bin").write_bytes(b"a\x00b")
    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")

    for path in ("../outside.md", "binary.bin", "missing.md"):
        with pytest.raises(SkillResourceError):
            loader.read_resource("review", path, token_limit=100)

    link = root / "link.md"
    try:
        os.symlink(outside, link)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(SkillResourceError):
        loader.read_resource("review", "link.md", token_limit=100)


def test_workspace_loader_rejects_package_replaced_after_discovery(
    tmp_path: Path,
) -> None:
    catalog = _workspace_catalog(tmp_path)
    loader = SkillLoader(catalog)
    package = catalog.resolve("review").root
    replacement = package.parent / "replacement"
    replacement.mkdir()
    (replacement / "SKILL.md").write_text(
        "---\nname: review\ndescription: Replaced\n---\noutside",
        encoding="utf-8",
    )
    original = package.parent / "original"
    package.rename(original)
    replacement.rename(package)

    with pytest.raises(SkillResourceError, match="changed after discovery"):
        loader.load("review")


def test_workspace_loader_rejects_skill_md_replaced_after_discovery(
    tmp_path: Path,
) -> None:
    catalog = _workspace_catalog(tmp_path)
    loader = SkillLoader(catalog)
    skill_md = catalog.resolve("review").root / "SKILL.md"
    replacement = skill_md.with_suffix(".new")
    replacement.write_text(
        "---\nname: review\ndescription: Replaced\n---\noutside",
        encoding="utf-8",
    )
    skill_md.unlink()
    replacement.rename(skill_md)

    with pytest.raises(SkillResourceError, match="changed after discovery"):
        loader.load("review")


def test_workspace_resource_revalidates_package_boundary(tmp_path: Path) -> None:
    catalog = _workspace_catalog(tmp_path)
    loader = SkillLoader(catalog)
    package = catalog.resolve("review").root
    moved = package.parent / "moved"
    package.rename(moved)
    package.mkdir()
    (package / "guide.md").write_text("replacement", encoding="utf-8")

    with pytest.raises(SkillResourceError, match="changed after discovery"):
        loader.read_resource("review", "guide.md", token_limit=100)


def test_workspace_resource_rejects_nested_reparse_point(tmp_path: Path) -> None:
    catalog = _workspace_catalog(tmp_path)
    loader = SkillLoader(catalog)
    package = catalog.resolve("review").root
    outside = tmp_path / "outside-resources"
    outside.mkdir()
    (outside / "secret.md").write_text("external sentinel", encoding="utf-8")
    _directory_link(outside, package / "references")

    with pytest.raises(SkillResourceError, match="links or reparse points"):
        loader.read_resource("review", "references/secret.md", token_limit=100)


def test_workspace_resource_rejects_file_replaced_before_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = _workspace_catalog(tmp_path)
    loader = SkillLoader(catalog)
    guide = catalog.resolve("review").root / "guide.md"
    replacement = guide.with_suffix(".replacement")
    replacement.write_text("external sentinel", encoding="utf-8")
    original = guide.with_suffix(".original")

    def replace_resource_before_open(
        path: Path,
        *,
        max_bytes: int,
        expected: FileFingerprint | None = None,
    ) -> BoundedFile:
        guide.rename(original)
        replacement.rename(guide)
        return read_bounded_file(path, max_bytes=max_bytes, expected=expected)

    monkeypatch.setattr(
        loader_module,
        "read_bounded_file",
        replace_resource_before_open,
    )

    with pytest.raises(SkillResourceError, match="changed after discovery"):
        loader.read_resource("review", "guide.md", token_limit=100)
