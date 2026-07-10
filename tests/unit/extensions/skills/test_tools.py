from pathlib import Path

from awesome_agent.core.tools.registry import ToolRegistry
from awesome_agent.extensions.skills import (
    SkillLoader,
    discover_skills,
    register_skill_tools,
)


def test_skill_tools_register_as_read_only_without_granting_capabilities(
    tmp_path: Path,
) -> None:
    root = tmp_path / "skills" / "review"
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text(
        "---\nname: review\ndescription: Review code\n"
        "allowed-tools: [execute]\n---\nbody",
        encoding="utf-8",
    )
    loader = SkillLoader(
        discover_skills(
            bundled_root=None,
            user_root=tmp_path / "skills",
            workspace_root=None,
            workspace_trusted=False,
        )
    )
    registry = ToolRegistry()
    register_skill_tools(registry, loader)

    specs = {item.name: item for item in registry.specifications()}
    assert set(specs) == {"load_skill", "read_skill_resource"}
    assert all(item.read_only for item in specs.values())
    assert registry.resolve("execute") is None
