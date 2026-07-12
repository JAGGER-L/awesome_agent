from __future__ import annotations

import os
import re
from collections.abc import Iterator
from pathlib import Path, PurePath
from typing import cast

from pydantic import BaseModel, Field, JsonValue

from awesome_agent.core.tools.builtins.read_file import MAX_FILE_BYTES
from awesome_agent.core.tools.context import ToolExecutionContext
from awesome_agent.core.tools.contracts import (
    ToolErrorCode,
    ToolOutput,
    ToolPresentation,
)
from awesome_agent.core.tools.errors import ExpectedToolFailure
from awesome_agent.core.tools.policy import SafeWorkspacePath, resolve_workspace_path


class GlobArguments(BaseModel):
    pattern: str = Field(min_length=1, max_length=500)
    path: str = "."
    max_results: int = Field(default=200, ge=1, le=1_000)


class GrepArguments(BaseModel):
    pattern: str = Field(min_length=1, max_length=1_000)
    path: str = "."
    include: str | None = Field(default=None, max_length=500)
    regex: bool = True
    case_sensitive: bool = True
    max_results: int = Field(default=100, ge=1, le=500)


def _search_root(
    context: ToolExecutionContext,
    requested: str,
) -> SafeWorkspacePath:
    lexical = context.workspace.canonical_path / Path(requested)
    if lexical.is_symlink():
        raise ExpectedToolFailure(
            ToolErrorCode.PERMISSION_DENIED,
            "Directory symlinks are not traversed.",
            metadata={"path": requested},
        )
    return resolve_workspace_path(
        context.workspace,
        requested,
        must_exist=True,
        expected_kind="directory",
    )


def _safe_text_files(
    root: SafeWorkspacePath,
    context: ToolExecutionContext,
) -> Iterator[tuple[str, str]]:
    workspace = context.workspace.canonical_path
    for current, directory_names, file_names in os.walk(
        root.resolved,
        followlinks=False,
    ):
        current_path = Path(current)
        safe_directories: list[str] = []
        for name in sorted(
            directory_names, key=lambda value: (value.casefold(), value)
        ):
            child = current_path / name
            if name == ".git" or child.is_symlink():
                continue
            relative = child.relative_to(workspace).as_posix()
            try:
                resolve_workspace_path(
                    context.workspace,
                    relative,
                    must_exist=True,
                    expected_kind="directory",
                )
            except ExpectedToolFailure:
                continue
            safe_directories.append(name)
        directory_names[:] = safe_directories

        for name in sorted(file_names, key=lambda value: (value.casefold(), value)):
            path = current_path / name
            if path.is_symlink():
                continue
            relative = path.relative_to(workspace).as_posix()
            try:
                safe = resolve_workspace_path(
                    context.workspace,
                    relative,
                    must_exist=True,
                    expected_kind="file",
                )
                if safe.resolved.stat().st_size > MAX_FILE_BYTES:
                    continue
                data = safe.resolved.read_bytes()
            except (ExpectedToolFailure, OSError):
                continue
            if b"\x00" in data:
                continue
            try:
                text = data.decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                continue
            yield relative, text


def _validate_pattern(pattern: str) -> None:
    candidate = PurePath(pattern)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ExpectedToolFailure(
            ToolErrorCode.WORKSPACE_ESCAPE,
            "Search pattern escapes the workspace boundary.",
        )


async def glob_files(
    arguments: BaseModel,
    context: ToolExecutionContext,
) -> ToolOutput:
    options = cast(GlobArguments, arguments)
    _validate_pattern(options.pattern)
    root = _search_root(context, options.path)
    matches: list[str] = []
    for relative, _ in _safe_text_files(root, context):
        relative_to_root = Path(relative).relative_to(root.relative).as_posix()
        if PurePath(relative_to_root).match(options.pattern):
            matches.append(relative)
            if len(matches) > options.max_results:
                break
    truncated = len(matches) > options.max_results
    bounded = matches[: options.max_results]
    content = "\n".join(bounded)
    return ToolOutput(
        content=content,
        metadata={"matches": cast(JsonValue, bounded), "truncated": truncated},
        presentation=ToolPresentation(
            verb="Glob",
            target=options.pattern,
            outcome="Found",
            summary=f"{len(bounded)} matches",
            detail=content[:4_000] or None,
        ),
    )


async def grep_files(
    arguments: BaseModel,
    context: ToolExecutionContext,
) -> ToolOutput:
    options = cast(GrepArguments, arguments)
    root = _search_root(context, options.path)
    compiled: re.Pattern[str] | None = None
    if options.regex:
        flags = 0 if options.case_sensitive else re.IGNORECASE
        try:
            compiled = re.compile(options.pattern, flags=flags)
        except re.error as error:
            raise ExpectedToolFailure(
                ToolErrorCode.INVALID_ARGUMENTS,
                f"Invalid regular expression: {error}",
            ) from error

    matches: list[dict[str, JsonValue]] = []
    for relative, text in _safe_text_files(root, context):
        relative_to_root = Path(relative).relative_to(root.relative).as_posix()
        if options.include is not None and not PurePath(relative_to_root).match(
            options.include
        ):
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            if compiled is not None:
                matched = compiled.search(line) is not None
            elif options.case_sensitive:
                matched = options.pattern in line
            else:
                matched = options.pattern.casefold() in line.casefold()
            if not matched:
                continue
            matches.append(
                {
                    "path": relative,
                    "line": line_number,
                    "text": line[:2_000],
                }
            )
            if len(matches) > options.max_results:
                break
        if len(matches) > options.max_results:
            break

    truncated = len(matches) > options.max_results
    bounded = matches[: options.max_results]
    content = "\n".join(
        f"{match['path']}:{match['line']}: {match['text']}" for match in bounded
    )
    return ToolOutput(
        content=content,
        metadata={"matches": cast(JsonValue, bounded), "truncated": truncated},
        presentation=ToolPresentation(
            verb="Grep",
            target=options.pattern,
            outcome="Found",
            summary=f"{len(bounded)} matches",
            detail=content[:4_000] or None,
        ),
    )
