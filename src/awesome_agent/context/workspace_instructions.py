from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from awesome_agent.context._safe_files import (
    FileChangedError,
    FileTooLargeError,
    PinnedPlainDirectory,
    UnsafePathError,
    lexical_absolute,
)
from awesome_agent.context.tokens import estimate_text

WORKSPACE_INSTRUCTION_FILE: Literal["AGENTS.md"] = "AGENTS.md"
WORKSPACE_INSTRUCTION_MAX_BYTES = 32 * 1024
WORKSPACE_INSTRUCTION_MAX_TOKENS = 8_192
WORKSPACE_INSTRUCTION_BUDGET_FRACTION = 0.10


class WorkspaceInstructionDiagnosticCode(StrEnum):
    UNSAFE_PATH = "workspace_instructions_unsafe_path"
    TOO_LARGE = "workspace_instructions_too_large"
    TOKEN_LIMIT = "workspace_instructions_token_limit"
    BINARY = "workspace_instructions_binary"
    NOT_UTF8 = "workspace_instructions_not_utf8"
    CHANGED = "workspace_instructions_changed"
    UNREADABLE = "workspace_instructions_unreadable"


class WorkspaceInstructionDiagnostic(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: WorkspaceInstructionDiagnosticCode
    source_id: Literal["AGENTS.md"] = WORKSPACE_INSTRUCTION_FILE
    message: str = Field(min_length=1, max_length=500)


class WorkspaceInstructionSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str = WORKSPACE_INSTRUCTION_FILE
    content: str | None = Field(
        default=None,
        max_length=WORKSPACE_INSTRUCTION_MAX_BYTES,
    )
    estimated_tokens: int = Field(default=0, ge=0)
    token_limit: int = Field(ge=1)
    diagnostic: WorkspaceInstructionDiagnostic | None = None


def load_workspace_instructions(
    *,
    workspace_root: Path,
    workspace_trusted: bool,
    effective_input_limit: int,
) -> WorkspaceInstructionSnapshot:
    if effective_input_limit <= 0:
        raise ValueError("effective_input_limit must be positive")
    token_limit = max(
        1,
        min(
            WORKSPACE_INSTRUCTION_MAX_TOKENS,
            int(effective_input_limit * WORKSPACE_INSTRUCTION_BUDGET_FRACTION),
        ),
    )
    empty = WorkspaceInstructionSnapshot(token_limit=token_limit)
    if not workspace_trusted:
        return empty

    root = lexical_absolute(workspace_root)
    try:
        with PinnedPlainDirectory(root, root) as pinned:
            try:
                bounded = pinned.read_file(
                    Path(WORKSPACE_INSTRUCTION_FILE),
                    max_bytes=WORKSPACE_INSTRUCTION_MAX_BYTES,
                )
            except FileNotFoundError:
                return empty
    except FileNotFoundError:
        return empty
    except UnsafePathError:
        return _failure(
            token_limit,
            WorkspaceInstructionDiagnosticCode.UNSAFE_PATH,
            "AGENTS.md was ignored because its path uses a link or reparse point.",
        )
    except FileTooLargeError:
        return _failure(
            token_limit,
            WorkspaceInstructionDiagnosticCode.TOO_LARGE,
            "AGENTS.md was ignored because it exceeds the 32 KiB limit.",
        )
    except FileChangedError:
        return _failure(
            token_limit,
            WorkspaceInstructionDiagnosticCode.CHANGED,
            "AGENTS.md was ignored because it changed while being read.",
        )
    except (OSError, ValueError):
        return _failure(
            token_limit,
            WorkspaceInstructionDiagnosticCode.UNREADABLE,
            "AGENTS.md was ignored because it could not be read safely.",
        )

    if b"\x00" in bounded.data:
        return _failure(
            token_limit,
            WorkspaceInstructionDiagnosticCode.BINARY,
            "AGENTS.md was ignored because binary content is not supported.",
        )
    try:
        content = _normalize_newlines(bounded.data.decode("utf-8", errors="strict"))
    except UnicodeDecodeError:
        return _failure(
            token_limit,
            WorkspaceInstructionDiagnosticCode.NOT_UTF8,
            "AGENTS.md was ignored because it is not UTF-8 text.",
        )
    estimated_tokens = estimate_text(content)
    if estimated_tokens > token_limit:
        return _failure(
            token_limit,
            WorkspaceInstructionDiagnosticCode.TOKEN_LIMIT,
            "AGENTS.md was ignored because it exceeds its context token allocation.",
        )
    return WorkspaceInstructionSnapshot(
        content=content,
        estimated_tokens=estimated_tokens,
        token_limit=token_limit,
    )


def _failure(
    token_limit: int,
    code: WorkspaceInstructionDiagnosticCode,
    message: str,
) -> WorkspaceInstructionSnapshot:
    return WorkspaceInstructionSnapshot(
        token_limit=token_limit,
        diagnostic=WorkspaceInstructionDiagnostic(code=code, message=message),
    )


def _normalize_newlines(content: str) -> str:
    return content.replace("\r\n", "\n").replace("\r", "\n")
