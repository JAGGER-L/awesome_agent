from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from yaml.events import (
    AliasEvent,
    CollectionEndEvent,
    CollectionStartEvent,
    ScalarEvent,
)

from awesome_agent.extensions.skills.models import SkillDescriptor, SkillSource

_MAX_YAML_DEPTH = 64
_MAX_YAML_NODES = 4_096
_MAX_YAML_ALIASES = 64
_ALLOWED_FIELDS = frozenset(
    {
        "name",
        "description",
        "allowed-tools",
        "license",
        "compatibility",
        "metadata",
    }
)


def decode_skill_manifest(data: bytes) -> str:
    if b"\x00" in data:
        raise ValueError("Binary Skill manifests are not supported")
    return (
        data.decode("utf-8", errors="strict").replace("\r\n", "\n").replace("\r", "\n")
    )


def parse_skill_manifest(
    text: str,
    *,
    source: SkillSource,
    root_path: Path,
    expected_name: str | None,
) -> SkillDescriptor:
    metadata = _frontmatter(text)
    unknown = metadata.keys() - _ALLOWED_FIELDS
    if unknown:
        raise ValueError(f"Unsupported Skill fields: {sorted(unknown)}")
    name = str(metadata.get("name") or "")
    if expected_name is not None and name != expected_name:
        raise ValueError("Skill name must match its directory")
    allowed = metadata.get("allowed-tools", [])
    allowed_tools: tuple[str, ...]
    if isinstance(allowed, str):
        allowed_tools = (allowed,)
    elif isinstance(allowed, list):
        allowed_tools = tuple(str(item) for item in allowed)
    else:
        raise ValueError("allowed-tools must be a string or list")
    raw_metadata = metadata.get("metadata", {})
    if not isinstance(raw_metadata, dict):
        raise ValueError("metadata must be a mapping")
    return SkillDescriptor(
        name=name,
        description=str(metadata.get("description") or ""),
        source=source,
        root=root_path,
        license=_optional_string(metadata.get("license")),
        compatibility=_optional_string(metadata.get("compatibility")),
        metadata={str(key): value for key, value in raw_metadata.items()},
        allowed_tools=allowed_tools,
    )


def _frontmatter(text: str) -> dict[str, Any]:
    if not text.startswith("---\n"):
        raise ValueError("SKILL.md requires YAML frontmatter")
    parts = text.split("---", 2)
    if len(parts) != 3:
        raise ValueError("SKILL.md frontmatter is incomplete")
    source = parts[1]
    try:
        _validate_yaml_events(source)
        parsed = yaml.safe_load(source)
        _validate_yaml_value(parsed)
    except (RecursionError, yaml.YAMLError) as error:
        raise ValueError("Skill frontmatter is not bounded valid YAML") from error
    if not isinstance(parsed, dict):
        raise ValueError("Skill frontmatter must be a mapping")
    return {str(key): value for key, value in parsed.items()}


def _validate_yaml_events(source: str) -> None:
    depth = 0
    nodes = 0
    aliases = 0
    for event in yaml.parse(source, Loader=yaml.SafeLoader):
        if isinstance(event, CollectionStartEvent):
            depth += 1
            nodes += 1
            if depth > _MAX_YAML_DEPTH:
                raise ValueError("Skill frontmatter exceeds the YAML depth limit")
        elif isinstance(event, CollectionEndEvent):
            depth -= 1
        elif isinstance(event, ScalarEvent):
            nodes += 1
        elif isinstance(event, AliasEvent):
            aliases += 1
            nodes += 1
            if aliases > _MAX_YAML_ALIASES:
                raise ValueError("Skill frontmatter exceeds the YAML alias limit")
        if nodes > _MAX_YAML_NODES:
            raise ValueError("Skill frontmatter exceeds the YAML node limit")
    if depth != 0:
        raise ValueError("Skill frontmatter has unbalanced YAML collections")


def _validate_yaml_value(value: object) -> None:
    nodes = 0
    active: set[int] = set()

    def walk(current: object, depth: int) -> None:
        nonlocal nodes
        nodes += 1
        if nodes > _MAX_YAML_NODES:
            raise ValueError("Skill frontmatter exceeds the YAML node limit")
        if depth > _MAX_YAML_DEPTH:
            raise ValueError("Skill frontmatter exceeds the YAML depth limit")
        if isinstance(current, dict):
            identity = id(current)
            if identity in active:
                raise ValueError("Skill frontmatter contains a recursive YAML alias")
            active.add(identity)
            try:
                for key, item in current.items():
                    walk(key, depth + 1)
                    walk(item, depth + 1)
            finally:
                active.remove(identity)
        elif isinstance(current, (list, set, tuple)):
            identity = id(current)
            if identity in active:
                raise ValueError("Skill frontmatter contains a recursive YAML alias")
            active.add(identity)
            try:
                for item in current:
                    walk(item, depth + 1)
            finally:
                active.remove(identity)

    walk(value, 0)


def _optional_string(value: object) -> str | None:
    return None if value is None else str(value)
