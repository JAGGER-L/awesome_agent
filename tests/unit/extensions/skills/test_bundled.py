from importlib.resources import files
from pathlib import Path

from awesome_agent.extensions.skills import SkillLoader, discover_skills

EXPECTED = {
    "debug": "Diagnose failures systematically from evidence",
    "git-workflow": "Prepare safe Git commits and workflow handoff",
    "init": "Initialize project guidance for this workspace",
    "review": "Review code for correctness, risk, and maintainability",
    "test": "Design and run focused validation for changes",
}


def test_installed_package_exposes_exactly_five_bundled_skills() -> None:
    bundled = files("awesome_agent.extensions.skills").joinpath("bundled")
    catalog = discover_skills(
        bundled_root=Path(str(bundled)),
        user_root=None,
        workspace_root=None,
        workspace_trusted=False,
    )

    descriptors = {item.name: item.description for item in catalog.descriptors()}

    assert descriptors == EXPECTED
    assert catalog.diagnostics() == ()


def test_bundled_skill_bodies_are_valid_bounded_and_modern() -> None:
    bundled = files("awesome_agent.extensions.skills").joinpath("bundled")
    catalog = discover_skills(
        bundled_root=Path(str(bundled)),
        user_root=None,
        workspace_root=None,
        workspace_trusted=False,
    )
    loader = SkillLoader(catalog)

    for name in EXPECTED:
        loaded = loader.load(name, token_limit=5_000)
        skill_text = loaded.body
        assert loaded.truncated is False
        assert loaded.estimated_tokens <= 5_000
        assert "requested_tools" not in skill_text
        assert "required_capabilities" not in skill_text
        assert "compatible_routes" not in skill_text
        assert "team:" not in skill_text
