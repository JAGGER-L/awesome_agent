from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from awesome_agent.extensions.skills.models import (
    SkillCatalog,
    SkillDescriptor,
    SkillDiagnostic,
    SkillSource,
)

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


def discover_skills(
    *,
    bundled_root: Path | None,
    user_root: Path | None,
    workspace_root: Path | None,
    workspace_trusted: bool,
    disabled: set[str] | None = None,
) -> SkillCatalog:
    disabled_names = disabled or set()
    discovered: dict[str, SkillDescriptor] = {}
    diagnostics: list[SkillDiagnostic] = []
    roots = (
        (SkillSource.BUNDLED, bundled_root),
        (SkillSource.USER, user_root),
        (
            SkillSource.WORKSPACE,
            workspace_root if workspace_trusted else None,
        ),
    )
    for source, root in roots:
        if root is None or not root.is_dir():
            continue
        for directory in sorted(root.iterdir(), key=lambda path: path.name):
            if not directory.is_dir():
                continue
            try:
                descriptor = _descriptor(directory, source)
            except (OSError, UnicodeError, ValueError, ValidationError) as error:
                diagnostics.append(
                    _diagnostic(
                        "invalid_skill",
                        source,
                        directory,
                        directory.name,
                        f"Invalid Skill metadata: {type(error).__name__}",
                    )
                )
                continue
            if descriptor.name in disabled_names:
                diagnostics.append(
                    _diagnostic(
                        "disabled",
                        source,
                        directory,
                        descriptor.name,
                        "Skill is disabled by user configuration.",
                    )
                )
                continue
            previous = discovered.get(descriptor.name)
            if previous is not None:
                diagnostics.append(
                    _diagnostic(
                        "shadowed",
                        previous.source,
                        previous.root,
                        previous.name,
                        f"Skill is shadowed by {source.value} source.",
                    )
                )
            discovered[descriptor.name] = descriptor
    return SkillCatalog(
        tuple(sorted(discovered.values(), key=lambda item: item.name)),
        tuple(diagnostics),
    )


def _descriptor(directory: Path, source: SkillSource) -> SkillDescriptor:
    metadata = _frontmatter(directory / "SKILL.md")
    unknown = metadata.keys() - _ALLOWED_FIELDS
    if unknown:
        raise ValueError(f"Unsupported Skill fields: {sorted(unknown)}")
    name = str(metadata.get("name") or "")
    if name != directory.name:
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
        root=directory.resolve(),
        license=_optional_string(metadata.get("license")),
        compatibility=_optional_string(metadata.get("compatibility")),
        metadata={str(key): value for key, value in raw_metadata.items()},
        allowed_tools=allowed_tools,
    )


def _frontmatter(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError("SKILL.md requires YAML frontmatter")
    parts = text.split("---", 2)
    if len(parts) != 3:
        raise ValueError("SKILL.md frontmatter is incomplete")
    parsed = yaml.safe_load(parts[1])
    if not isinstance(parsed, dict):
        raise ValueError("Skill frontmatter must be a mapping")
    return {str(key): value for key, value in parsed.items()}


def _optional_string(value: object) -> str | None:
    return None if value is None else str(value)


def _diagnostic(
    code: str,
    source: SkillSource,
    path: Path,
    name: str | None,
    message: str,
) -> SkillDiagnostic:
    return SkillDiagnostic(
        code=code,
        source=source,
        path=str(path),
        name=name,
        message=message,
    )
