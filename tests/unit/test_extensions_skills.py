from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

from awesome_agent.domain.enums import RiskLevel
from awesome_agent.extensions.models import (
    ExtensionCatalog,
    ExtensionSkillInventoryItem,
)
from awesome_agent.extensions.service import ExtensionDiscoveryService
from awesome_agent.extensions.skills import SkillDirectorySource, SkillRuntimeView
from awesome_agent.extensions.sources import (
    ExtensionSource,
    ExtensionSourceFactory,
)
from awesome_agent.runtime.team_assignments import (
    TeamAssignment,
    TeamAssignmentKind,
)

SKILL_TEXT = """---
id: repository-inspection
version: "1"
risk_level: low
compatible_actor_kinds: ["leader", "teammate", "subagent"]
compatible_routes: ["solo-readonly", "team-role"]
requested_tools: ["repo.search", "repo.read"]
required_capabilities: ["repository:read"]
---

# Repository Inspection

Use repository search and bounded reads to gather evidence before answering.
"""


def test_skill_manifest_parses_front_matter_and_body(tmp_path: Path) -> None:
    skill_dir = tmp_path / "repository-inspection"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(SKILL_TEXT, encoding="utf-8")

    manifest = SkillDirectorySource(tmp_path).load_skill("repository-inspection")

    assert manifest.id == "repository-inspection"
    assert manifest.version == "1"
    assert manifest.risk_level is RiskLevel.LOW
    assert manifest.requested_tools == ["repo.search", "repo.read"]
    assert manifest.required_capabilities == {"repository:read"}
    assert "gather evidence" in manifest.instructions


def test_skill_requests_tools_without_granting_them() -> None:
    view = SkillRuntimeView.from_allowed_skills(
        allowed_skill_ids=["repository-inspection"],
        catalog=_catalog_with_skill(requested_tools=["repo.read"]),
        assignment=_assignment(allowed_tools=[]),
        actor_kind="teammate",
        route="team-role",
    )

    assert view.requested_tools == ["repo.read"]
    assert view.granted_tools == []
    assert view.denied_tool_reasons == {"repo.read": "not_assigned"}


def test_skill_directory_source_publishes_catalog_inventory(tmp_path: Path) -> None:
    skill_dir = tmp_path / "repository-inspection"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(SKILL_TEXT, encoding="utf-8")
    source = ExtensionSourceFactory().create(
        {
            "id": "local-skills",
            "type": "skill_directory",
            "trust": "project",
            "path": tmp_path,
        }
    )

    catalog = _run_publish(source)

    assert catalog.skills[0].id == "repository-inspection"
    assert catalog.skills[0].source_id == "local-skills"
    assert catalog.skills[0].requested_tools == ["repo.search", "repo.read"]


def test_skill_runtime_view_enforces_actor_and_route_compatibility() -> None:
    view = SkillRuntimeView.from_allowed_skills(
        allowed_skill_ids=["repository-inspection"],
        catalog=_catalog_with_skill(
            requested_tools=["repo.read"],
            compatible_actor_kinds=["leader"],
            compatible_routes=["solo-readonly"],
        ),
        assignment=_assignment(allowed_tools=["repo.read"]),
        actor_kind="teammate",
        route="team-role",
    )

    assert view.skill_ids == []
    assert view.requested_tools == []
    assert view.denied_skill_reasons == {
        "repository-inspection": "incompatible_actor"
    }


def _catalog_with_skill(
    *,
    requested_tools: list[str],
    compatible_actor_kinds: list[str] | None = None,
    compatible_routes: list[str] | None = None,
) -> ExtensionCatalog:
    return ExtensionCatalog(
        version="ext_123",
        skills=[
            ExtensionSkillInventoryItem(
                id="repository-inspection",
                source_id="local-skills",
                version="1",
                instructions="Use repository evidence.",
                requested_tools=requested_tools,
                required_capabilities={"repository:read"},
                compatible_actor_kinds=set(
                    compatible_actor_kinds or ["leader", "teammate", "subagent"]
                ),
                compatible_routes=set(compatible_routes or ["team-role"]),
                risk_level=RiskLevel.LOW,
            )
        ],
    )


def _assignment(*, allowed_tools: list[str]) -> TeamAssignment:
    root_run_id = uuid4()
    return TeamAssignment(
        root_run_id=root_run_id,
        parent_run_id=root_run_id,
        child_run_id=uuid4(),
        kind=TeamAssignmentKind.TEAMMATE,
        role_profile="teammate",
        runtime_route="team-role",
        goal="Inspect repository",
        allowed_tools=allowed_tools,
        allowed_skills=["repository-inspection"],
    )


def _run_publish(source: ExtensionSource) -> ExtensionCatalog:
    return asyncio.run(ExtensionDiscoveryService([source]).publish())
