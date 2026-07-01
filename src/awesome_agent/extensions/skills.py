from __future__ import annotations

from pathlib import Path

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, Field

from awesome_agent.domain.enums import RiskLevel
from awesome_agent.extensions.models import (
    ExtensionCatalog,
    ExtensionSkillInventoryItem,
)
from awesome_agent.runtime.team_assignments import TeamAssignment


class SkillManifestError(ValueError):
    pass


class SkillDirectorySource:
    def __init__(self, root: Path, *, source_id: str = "local-skills") -> None:
        self.root = root.resolve()
        self.source_id = source_id

    def load_skill(self, skill_id: str) -> ExtensionSkillInventoryItem:
        skill_path = (self.root / skill_id / "SKILL.md").resolve()
        if not skill_path.is_relative_to(self.root):
            raise SkillManifestError("Skill path escapes the configured root.")
        if not skill_path.is_file():
            raise SkillManifestError(f"Missing SKILL.md for skill {skill_id}.")
        metadata, instructions = _parse_skill_markdown(
            skill_path.read_text(encoding="utf-8")
        )
        manifest_id = str(metadata.get("id") or skill_id)
        return ExtensionSkillInventoryItem(
            id=manifest_id,
            source_id=self.source_id,
            version=str(metadata.get("version") or "1"),
            instructions=instructions.strip(),
            context_refs=_string_list(metadata.get("context_refs")),
            requested_tools=_string_list(metadata.get("requested_tools")),
            required_capabilities=set(_string_list(metadata.get("required_capabilities"))),
            compatible_actor_kinds=set(
                _string_list(metadata.get("compatible_actor_kinds"))
            ),
            compatible_routes=set(_string_list(metadata.get("compatible_routes"))),
            risk_level=RiskLevel(str(metadata.get("risk_level") or RiskLevel.LOW)),
        )

    def load_all(self) -> list[ExtensionSkillInventoryItem]:
        if not self.root.is_dir():
            raise SkillManifestError("Skill directory root does not exist.")
        return [
            self.load_skill(path.name)
            for path in sorted(self.root.iterdir())
            if path.is_dir()
        ]


class ResolvedSkill(BaseModel):
    id: str
    version: str
    instructions: str = ""


class SkillRuntimeView(BaseModel):
    skill_ids: list[str] = Field(default_factory=list)
    skills: list[ResolvedSkill] = Field(default_factory=list)
    requested_tools: list[str] = Field(default_factory=list)
    granted_tools: list[str] = Field(default_factory=list)
    required_capabilities: set[str] = Field(default_factory=set)
    denied_tool_reasons: dict[str, str] = Field(default_factory=dict)
    denied_skill_reasons: dict[str, str] = Field(default_factory=dict)

    @classmethod
    def from_allowed_skills(
        cls,
        *,
        allowed_skill_ids: list[str],
        catalog: ExtensionCatalog,
        assignment: TeamAssignment,
        actor_kind: str,
        route: str,
    ) -> SkillRuntimeView:
        allowed = set(allowed_skill_ids)
        manifests = {skill.id: skill for skill in catalog.skills}
        skill_ids: list[str] = []
        resolved_skills: list[ResolvedSkill] = []
        requested_tools: list[str] = []
        granted_tools: list[str] = []
        required_capabilities: set[str] = set()
        denied_tool_reasons: dict[str, str] = {}
        denied_skill_reasons: dict[str, str] = {}
        assignment_tools = set(assignment.allowed_tools)
        for skill_id in allowed_skill_ids:
            manifest = manifests.get(skill_id)
            if manifest is None:
                denied_skill_reasons[skill_id] = "missing_manifest"
                continue
            if manifest.compatible_actor_kinds and (
                actor_kind not in manifest.compatible_actor_kinds
            ):
                denied_skill_reasons[skill_id] = "incompatible_actor"
                continue
            if manifest.compatible_routes and route not in manifest.compatible_routes:
                denied_skill_reasons[skill_id] = "incompatible_route"
                continue
            if skill_id not in allowed:
                denied_skill_reasons[skill_id] = "not_allowed"
                continue
            skill_ids.append(skill_id)
            resolved_skills.append(
                ResolvedSkill(
                    id=manifest.id,
                    version=manifest.version,
                    instructions=manifest.instructions,
                )
            )
            required_capabilities.update(manifest.required_capabilities)
            for tool_name in manifest.requested_tools:
                if tool_name not in requested_tools:
                    requested_tools.append(tool_name)
                if tool_name in assignment_tools and tool_name not in granted_tools:
                    granted_tools.append(tool_name)
                elif tool_name not in assignment_tools:
                    denied_tool_reasons[tool_name] = "not_assigned"
        return cls(
            skill_ids=skill_ids,
            skills=resolved_skills,
            requested_tools=requested_tools,
            granted_tools=granted_tools,
            required_capabilities=required_capabilities,
            denied_tool_reasons=denied_tool_reasons,
            denied_skill_reasons=denied_skill_reasons,
        )


def _parse_skill_markdown(markdown: str) -> tuple[dict[str, object], str]:
    if not markdown.startswith("---\n"):
        return {}, markdown
    _, metadata_text, body = markdown.split("---", 2)
    metadata = yaml.safe_load(metadata_text) or {}
    if not isinstance(metadata, dict):
        raise SkillManifestError("Skill front matter must be a mapping.")
    return {str(key): value for key, value in metadata.items()}, body


def _string_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value]
    raise SkillManifestError("Skill manifest field must be a string or list.")
