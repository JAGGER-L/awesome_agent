from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from awesome_agent.modeling import ModelMessage


class ContextSourceKind(StrEnum):
    PRODUCT_INSTRUCTIONS = "product_instructions"
    WORKSPACE_INSTRUCTIONS = "workspace_instructions"
    SKILL = "skill"
    USER_MEMORY = "user_memory"
    WORKSPACE_MEMORY = "workspace_memory"
    MEM0 = "mem0"
    THREAD_SUMMARY = "thread_summary"
    RECENT_TURNS = "recent_turns"
    DIRECT_COMMAND = "direct_command"
    EXPLICIT_PATH = "explicit_path"
    CURRENT_INPUT = "current_input"
    OPEN_TOOL_CHAIN = "open_tool_chain"


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
