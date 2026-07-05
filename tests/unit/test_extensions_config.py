from __future__ import annotations

from pathlib import Path

import pytest

from awesome_agent.extensions.catalog import empty_extension_catalog
from awesome_agent.extensions.config import (
    build_project_extension_catalog,
    load_project_extension_config,
)

SKILL_TEXT = """---
id: {skill_id}
version: "1"
requested_tools: ["repo.read"]
---

# Repository Inspection

Read bounded repository evidence.
"""


async def test_default_project_skills_source_is_discovered(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills" / "repository-inspection"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        SKILL_TEXT.format(skill_id="repository-inspection"),
        encoding="utf-8",
    )

    catalog = await build_project_extension_catalog(
        project_root=tmp_path,
        home=tmp_path / "home",
    )

    assert catalog.skills[0].id == "repository-inspection"
    assert catalog.skills[0].source_id == "project-skills"


async def test_user_and_project_skills_are_discovered_together(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    user_skill_dir = home / "skills" / "user-skill"
    project_skill_dir = project / "skills" / "project-skill"
    user_skill_dir.mkdir(parents=True)
    project_skill_dir.mkdir(parents=True)
    (user_skill_dir / "SKILL.md").write_text(
        SKILL_TEXT.format(skill_id="user-skill"),
        encoding="utf-8",
    )
    (project_skill_dir / "SKILL.md").write_text(
        SKILL_TEXT.format(skill_id="project-skill"),
        encoding="utf-8",
    )

    catalog = await build_project_extension_catalog(
        project_root=project,
        home=home,
    )

    assert {skill.id for skill in catalog.skills} == {"user-skill", "project-skill"}
    assert {skill.source_id for skill in catalog.skills} == {
        "user-skills",
        "project-skills",
    }


def test_project_extension_config_loads_sources_and_relative_roots(
    tmp_path: Path,
) -> None:
    (tmp_path / "custom-skills").mkdir()
    (tmp_path / "awesome-agent.yaml").write_text(
        """
version: 1
extensions:
  skills:
    auto_discover_project_skills: false
    roots:
      - custom-skills
  sources:
    - id: extra-skills
      type: skill_directory
      path: extra-skills
      trust: user
      required: false
""",
        encoding="utf-8",
    )

    config = load_project_extension_config(tmp_path, home=tmp_path / "home")

    assert [source.id for source in config.sources] == [
        "project-skills",
        "extra-skills",
    ]
    assert config.sources[0].path == tmp_path / "custom-skills"
    assert config.sources[1].path == tmp_path / "extra-skills"


def test_mcp_stdio_env_pass_names_do_not_store_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "secret-token")
    home = tmp_path / "awesome-home"
    home.mkdir()
    (home / "awesome-agent.yaml").write_text(
        """
version: 1
extensions:
  sources:
    - id: github
      type: mcp_stdio
      command: uvx
      args: ["github-mcp-server"]
      trust: user
      required: false
      env:
        pass:
          - GITHUB_TOKEN
""",
        encoding="utf-8",
    )

    config = load_project_extension_config(tmp_path / "project", home=home)

    assert config.sources[0].env is not None
    assert config.sources[0].env.pass_names == ["GITHUB_TOKEN"]
    assert "secret-token" not in config.model_dump_json().lower()


def test_project_mcp_sources_are_ignored_when_user_mcp_sources_exist(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir(parents=True)
    project.mkdir()
    (home / "awesome-agent.yaml").write_text(
        """
version: 1
extensions:
  sources:
    - id: user-github
      type: mcp_stdio
      command: uvx
      args: ["github-mcp-server"]
      trust: user
      required: false
""",
        encoding="utf-8",
    )
    (project / "awesome-agent.yaml").write_text(
        """
version: 1
extensions:
  sources:
    - id: project-playwright
      type: mcp_stdio
      command: npx
      args: ["@playwright/mcp"]
      trust: project
      required: false
""",
        encoding="utf-8",
    )

    config = load_project_extension_config(project, home=home)

    assert [source.id for source in config.sources] == ["user-github"]


async def test_missing_config_without_skills_returns_empty_catalog(
    tmp_path: Path,
) -> None:
    catalog = await build_project_extension_catalog(
        project_root=tmp_path,
        home=tmp_path / "home",
    )

    assert catalog == empty_extension_catalog()
