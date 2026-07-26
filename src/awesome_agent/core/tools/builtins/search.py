from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path, PurePath
from typing import cast

from pydantic import BaseModel, Field, JsonValue

from awesome_agent.core.filesystem import WorkspaceFileTooLarge
from awesome_agent.core.tools.builtins.file_enumerator import (
    EnumeratedFile,
    ScanCancellation,
    ScanCancelled,
    enumerate_workspace_files,
)
from awesome_agent.core.tools.builtins.read_file import MAX_FILE_BYTES
from awesome_agent.core.tools.context import ToolExecutionContext
from awesome_agent.core.tools.contracts import (
    ToolArguments,
    ToolErrorCode,
    ToolOutput,
    ToolPresentation,
)
from awesome_agent.core.tools.errors import ExpectedToolFailure
from awesome_agent.core.tools.policy import SafeWorkspacePath, resolve_workspace_path


class GlobArguments(ToolArguments):
    pattern: str = Field(min_length=1, max_length=500)
    path: str = "."
    max_results: int = Field(default=200, ge=1, le=1_000)


class GrepArguments(ToolArguments):
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
    return resolve_workspace_path(
        context.workspace,
        requested,
        must_exist=True,
        expected_kind="directory",
    )


def _glob_scan_root(
    root: SafeWorkspacePath,
    pattern: str,
    context: ToolExecutionContext,
) -> SafeWorkspacePath | None:
    fixed_parts: list[str] = []
    for part in PurePath(pattern).parts[:-1]:
        if any(character in part for character in "*?["):
            break
        fixed_parts.append(part)
    if not fixed_parts:
        return root
    relative = root.relative.joinpath(*fixed_parts).as_posix()
    try:
        return resolve_workspace_path(
            context.workspace,
            relative,
            must_exist=True,
            expected_kind="directory",
        )
    except ExpectedToolFailure as error:
        if error.code is ToolErrorCode.NOT_FOUND:
            return None
        raise


def validate_glob_pattern(pattern: str) -> None:
    candidate = PurePath(pattern)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ExpectedToolFailure(
            ToolErrorCode.WORKSPACE_ESCAPE,
            "Search pattern escapes the workspace boundary.",
        )


def admit_glob(
    arguments: BaseModel,
    context: ToolExecutionContext,
) -> None:
    options = cast(GlobArguments, arguments)
    _search_root(context, options.path)
    validate_glob_pattern(options.pattern)


def admit_grep(
    arguments: BaseModel,
    context: ToolExecutionContext,
) -> None:
    options = cast(GrepArguments, arguments)
    _search_root(context, options.path)


async def _run_scan[T](scan: Callable[[ScanCancellation], T]) -> T:
    cancellation = ScanCancellation()
    worker = asyncio.create_task(asyncio.to_thread(scan, cancellation))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        cancellation.cancel()
        with suppress(ScanCancelled):
            await worker
        raise


async def glob_files(
    arguments: BaseModel,
    context: ToolExecutionContext,
) -> ToolOutput:
    options = cast(GlobArguments, arguments)
    validate_glob_pattern(options.pattern)
    root = _search_root(context, options.path)
    scan_root = _glob_scan_root(root, options.pattern, context)

    def scan(cancellation: ScanCancellation) -> tuple[list[str], bool]:
        if scan_root is None:
            return [], False
        matches: list[str] = []
        for item in enumerate_workspace_files(
            scan_root,
            context,
            cancellation=cancellation,
            prune_defaults=True,
        ):
            relative_to_root = Path(item.relative).relative_to(root.relative).as_posix()
            if PurePath(relative_to_root).match(options.pattern):
                matches.append(item.relative)
                if len(matches) > options.max_results:
                    break
        return matches[: options.max_results], len(matches) > options.max_results

    bounded, truncated = await _run_scan(scan)
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

    def scan(
        cancellation: ScanCancellation,
    ) -> tuple[list[dict[str, JsonValue]], bool]:
        matches: list[dict[str, JsonValue]] = []
        for item in enumerate_workspace_files(
            root,
            context,
            cancellation=cancellation,
            prune_defaults=True,
        ):
            relative_to_root = Path(item.relative).relative_to(root.relative).as_posix()
            if options.include is not None and not PurePath(relative_to_root).match(
                options.include
            ):
                continue
            text = _read_searchable_text(item)
            if text is None:
                continue
            for line_number, line in enumerate(text.splitlines(), start=1):
                cancellation.raise_if_cancelled()
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
                        "path": item.relative,
                        "line": line_number,
                        "text": line[:2_000],
                    }
                )
                if len(matches) > options.max_results:
                    break
            if len(matches) > options.max_results:
                break
        return matches[: options.max_results], len(matches) > options.max_results

    bounded, truncated = await _run_scan(scan)
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


def _read_searchable_text(item: EnumeratedFile) -> str | None:
    try:
        data = item.read_bytes(max_bytes=MAX_FILE_BYTES)
    except WorkspaceFileTooLarge:
        return None
    if b"\x00" in data:
        return None
    try:
        return data.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return None
