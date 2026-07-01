from __future__ import annotations

import hashlib
import json
from typing import Any, cast

from pydantic import BaseModel

from awesome_agent.extensions.models import (
    ExtensionCatalog,
    ExtensionSkillInventoryItem,
    ExtensionSourceSnapshot,
    ExtensionToolInventoryItem,
)


def publish_catalog(
    *,
    sources: list[ExtensionSourceSnapshot],
    tools: list[ExtensionToolInventoryItem],
    skills: list[ExtensionSkillInventoryItem],
) -> ExtensionCatalog:
    ordered_sources = sorted(sources, key=lambda source: source.id)
    ordered_tools = sorted(tools, key=lambda tool: (tool.source_id, tool.name))
    ordered_skills = sorted(skills, key=lambda skill: (skill.source_id, skill.id))
    return ExtensionCatalog(
        version=_catalog_version(
            sources=ordered_sources,
            tools=ordered_tools,
            skills=ordered_skills,
        ),
        sources=ordered_sources,
        tools=ordered_tools,
        skills=ordered_skills,
    )


def empty_extension_catalog() -> ExtensionCatalog:
    return publish_catalog(sources=[], tools=[], skills=[])


def _catalog_version(
    *,
    sources: list[ExtensionSourceSnapshot],
    tools: list[ExtensionToolInventoryItem],
    skills: list[ExtensionSkillInventoryItem],
) -> str:
    payload = {
        "sources": [
            {
                "id": source.id,
                "type": source.type.value,
                "trust": source.trust.value,
                "health_status": source.health.status.value,
            }
            for source in sources
        ],
        "tools": [_stable_model_dump(tool) for tool in tools],
        "skills": [_stable_model_dump(skill) for skill in skills],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return f"ext_{hashlib.sha256(encoded).hexdigest()[:16]}"


def _stable_model_dump(model: BaseModel) -> dict[str, Any]:
    dumped = model.model_dump(mode="json")
    return cast(dict[str, Any], _normalize_sets(dumped))


def _normalize_sets(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _normalize_sets(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        return [_normalize_sets(item) for item in value]
    if isinstance(value, set):
        return sorted(value)
    return value
