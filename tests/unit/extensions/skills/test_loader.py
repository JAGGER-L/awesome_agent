import os
from pathlib import Path

import pytest

from awesome_agent.extensions.skills import (
    SkillLoader,
    SkillResourceError,
    discover_skills,
)


def _catalog(tmp_path: Path):
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
