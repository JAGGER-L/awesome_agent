from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from awesome_agent.core.safe_files import FileFingerprint, FileIdentity

_TOOL_NAME = re.compile(r"^[a-z][a-z0-9_.-]{0,199}$")


class SkillSource(StrEnum):
    BUNDLED = "bundled"
    USER = "user"
    WORKSPACE = "workspace"


class SkillDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    description: str = Field(min_length=1, max_length=500)
    source: SkillSource
    root: Path
    license: str | None = Field(default=None, max_length=500)
    compatibility: str | None = Field(default=None, max_length=500)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    allowed_tools: tuple[str, ...] = Field(default=(), max_length=128)

    @field_validator("allowed_tools")
    @classmethod
    def validate_allowed_tools(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _validate_allowed_tools(value)


class SkillIdentitySnapshot(BaseModel):
    """Path-independent identity frozen into one Turn's context."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    source: SkillSource
    identity: str = Field(pattern=r"^skill-v1-sha256:[a-f0-9]{64}$")
    allowed_tools: tuple[str, ...] = Field(default=(), max_length=128)

    @field_validator("allowed_tools")
    @classmethod
    def validate_allowed_tools(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _validate_allowed_tools(value)


class SkillDiagnostic(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str = Field(min_length=1, max_length=128)
    source: SkillSource
    path: str = Field(max_length=2_000)
    name: str | None = Field(default=None, max_length=64)
    message: str = Field(min_length=1, max_length=1_000)


class SkillNotFound(LookupError):
    pass


@dataclass(frozen=True, slots=True)
class _SkillBoundary:
    anchor: Path
    anchor_identity: FileIdentity
    source_root: Path
    source_root_identity: FileIdentity
    package_root: Path
    package_root_identity: FileIdentity
    package_component_identities: tuple[FileIdentity, ...]
    skill_file_fingerprint: FileFingerprint
    skill_file_content_hash: str
    snapshot: SkillIdentitySnapshot


def _identity_snapshot(
    descriptor: SkillDescriptor,
    *,
    fingerprint: FileFingerprint,
    content: bytes,
) -> SkillIdentitySnapshot:
    """Build a logical identity from one safely pinned SKILL.md snapshot."""

    canonical = json.dumps(
        {
            "descriptor": descriptor.model_dump(mode="json", exclude={"root"}),
            "skill_file": {
                "fingerprint": {
                    "device": fingerprint.identity.device,
                    "file_type": fingerprint.identity.file_type,
                    "inode": fingerprint.identity.inode,
                    "modified_ns": fingerprint.modified_ns,
                    "size": fingerprint.size,
                },
                "sha256": sha256(content).hexdigest(),
            },
            "version": 1,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return SkillIdentitySnapshot(
        name=descriptor.name,
        source=descriptor.source,
        identity=f"skill-v1-sha256:{sha256(canonical).hexdigest()}",
        allowed_tools=descriptor.allowed_tools,
    )


def _validate_allowed_tools(value: tuple[str, ...]) -> tuple[str, ...]:
    if len(value) != len(set(value)):
        raise ValueError("Skill allowed-tool names must be unique.")
    if any(_TOOL_NAME.fullmatch(name) is None for name in value):
        raise ValueError("Skill allowed-tool name is invalid.")
    return value


class SkillCatalog:
    def __init__(
        self,
        descriptors: tuple[SkillDescriptor, ...],
        diagnostics: tuple[SkillDiagnostic, ...],
        boundaries: dict[str, _SkillBoundary] | None = None,
    ) -> None:
        self._descriptors = tuple(sorted(descriptors, key=lambda item: item.name))
        self._descriptor_by_name = {
            descriptor.name: descriptor for descriptor in self._descriptors
        }
        self._diagnostics = diagnostics
        self._boundaries = dict(boundaries or {})

    def descriptors(
        self,
        *,
        limit: int | None = None,
    ) -> tuple[SkillDescriptor, ...]:
        if limit is None:
            return self._descriptors
        if limit < 0:
            raise ValueError("Skill descriptor limit must be non-negative.")
        return self._descriptors[:limit]

    def diagnostics(self) -> tuple[SkillDiagnostic, ...]:
        return self._diagnostics

    def resolve(self, name: str) -> SkillDescriptor:
        descriptor = self._descriptor_by_name.get(name)
        if descriptor is None:
            raise SkillNotFound(name)
        return descriptor

    def identity_snapshot(self, name: str) -> SkillIdentitySnapshot:
        self.resolve(name)
        boundary = self._boundaries.get(name)
        if boundary is None:
            raise SkillNotFound(name)
        return boundary.snapshot

    def identity_snapshots(
        self,
        *,
        limit: int | None = None,
    ) -> tuple[SkillIdentitySnapshot, ...]:
        descriptors = self.descriptors(limit=limit)
        snapshots: list[SkillIdentitySnapshot] = []
        for descriptor in descriptors:
            boundary = self._boundaries.get(descriptor.name)
            if boundary is None:
                raise SkillNotFound(descriptor.name)
            snapshots.append(boundary.snapshot)
        return tuple(snapshots)

    def _boundary(self, name: str) -> _SkillBoundary | None:
        return self._boundaries.get(name)
