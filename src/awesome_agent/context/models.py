from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from enum import StrEnum
from typing import Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from awesome_agent.modeling import ModelMessage


class ContextSourceKind(StrEnum):
    PRODUCT_INSTRUCTIONS = "product_instructions"
    WORKSPACE_INSTRUCTIONS = "workspace_instructions"
    SKILL = "skill"
    SKILL_CATALOG = "skill_catalog"
    USER_MEMORY = "user_memory"
    WORKSPACE_MEMORY = "workspace_memory"
    MEM0 = "mem0"
    THREAD_SUMMARY = "thread_summary"
    RECENT_TURNS = "recent_turns"
    DIRECT_COMMAND = "direct_command"
    EXPLICIT_PATH = "explicit_path"
    CURRENT_INPUT = "current_input"
    OPEN_TOOL_CHAIN = "open_tool_chain"


_TOOL_NAME = re.compile(r"^[a-z][a-z0-9_.-]{0,199}$")
_SKILL_CONTEXT_KINDS = frozenset(
    {ContextSourceKind.SKILL, ContextSourceKind.SKILL_CATALOG}
)


class ContextSkillIdentity(BaseModel):
    """Provider-neutral Skill identity frozen into a Turn context manifest."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    source: Literal["bundled", "user", "workspace"]
    identity: str = Field(pattern=r"^skill-v1-sha256:[a-f0-9]{64}$")
    allowed_tools: tuple[str, ...] = Field(default=(), max_length=128)

    @field_validator("allowed_tools")
    @classmethod
    def validate_allowed_tools(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("Frozen Skill allowed-tool names must be unique.")
        if any(_TOOL_NAME.fullmatch(name) is None for name in value):
            raise ValueError("Frozen Skill allowed-tool name is invalid.")
        return value


def _validate_skill_identities(
    *,
    kind: ContextSourceKind,
    source_id: str,
    identities: tuple[ContextSkillIdentity, ...],
    allow_legacy_missing: bool = False,
) -> None:
    if identities and kind not in _SKILL_CONTEXT_KINDS:
        raise ValueError("Only Skill context may carry frozen Skill identities.")
    if kind is ContextSourceKind.SKILL:
        valid_identity = len(identities) == 1 and identities[0].name == source_id
        if not valid_identity and not (allow_legacy_missing and not identities):
            raise ValueError("A named Skill source must carry its own identity.")
    if kind is ContextSourceKind.SKILL_CATALOG:
        if source_id != "auto":
            raise ValueError("The automatic Skill Catalog source ID must be auto.")
        names = tuple(identity.name for identity in identities)
        if len(names) != len(set(names)) or names != tuple(sorted(names)):
            raise ValueError(
                "Frozen Skill Catalog identities must be unique and name ordered."
            )


class ContextManifestItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: ContextSourceKind
    source_id: str = Field(min_length=1, max_length=512)
    order: int = Field(ge=0)
    estimated_tokens: int = Field(ge=0)
    truncated: bool
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    covered_sequence_start: int | None = Field(default=None, ge=1)
    covered_sequence_end: int | None = Field(default=None, ge=1)
    skill_identities: tuple[ContextSkillIdentity, ...] = Field(
        default=(),
        max_length=64,
    )

    @model_validator(mode="after")
    def validate_skill_identity_scope(self) -> Self:
        _validate_skill_identities(
            kind=self.kind,
            source_id=self.source_id,
            identities=self.skill_identities,
            # Pre-identity named Skill checkpoints remain parseable only so the
            # recovery validator can downgrade them to context-only access.
            allow_legacy_missing=True,
        )
        return self


class ContextSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: ContextSourceKind
    source_id: str = Field(min_length=1, max_length=512)
    content: str = Field(max_length=1_000_000)
    role: Literal["system", "user", "assistant"] = "user"
    mandatory: bool = False
    token_budget: int | None = Field(default=None, ge=1)
    covered_sequence_start: int | None = Field(default=None, ge=1)
    covered_sequence_end: int | None = Field(default=None, ge=1)
    skill_identities: tuple[ContextSkillIdentity, ...] = Field(
        default=(),
        max_length=64,
    )
    legacy_skill_identity_missing: bool = False

    @model_validator(mode="after")
    def validate_skill_identity_scope(self) -> Self:
        if (
            self.legacy_skill_identity_missing
            and self.kind is not ContextSourceKind.SKILL
        ):
            raise ValueError("Only a named Skill may use legacy context-only access.")
        _validate_skill_identities(
            kind=self.kind,
            source_id=self.source_id,
            identities=self.skill_identities,
            allow_legacy_missing=self.legacy_skill_identity_missing,
        )
        return self


class PreparedContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    messages: tuple[ModelMessage, ...]
    manifest: tuple[ContextManifestItem, ...]
    estimated_input_tokens: int = Field(ge=0)
    effective_input_limit: int = Field(gt=0)
    compression_recommended: bool


class ContextRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sources: tuple[ContextSource, ...]
    configured_total_tokens: int = Field(gt=0)
    model_context_limit: int = Field(gt=0)
    reserved_input_tokens: int = Field(default=0, ge=0)


def skill_identities_from_manifest(
    manifest: Iterable[Mapping[str, object] | ContextManifestItem],
) -> tuple[ContextSkillIdentity, ...]:
    """Return frozen Skill grants, failing closed for malformed manifests."""

    identities: list[ContextSkillIdentity] = []
    seen_names: set[str] = set()
    try:
        for raw_item in manifest:
            item = (
                raw_item
                if isinstance(raw_item, ContextManifestItem)
                else ContextManifestItem.model_validate(raw_item)
            )
            for identity in item.skill_identities:
                if identity.name in seen_names:
                    return ()
                seen_names.add(identity.name)
                identities.append(identity)
    except (TypeError, ValueError):
        return ()
    return tuple(identities)
