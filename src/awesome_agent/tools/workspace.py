from __future__ import annotations

import fnmatch
import json
import os
import re
import shlex
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Any

from pydantic import BaseModel, Field, model_validator

from awesome_agent.domain.enums import RiskLevel
from awesome_agent.safety.redaction import redact_text, redaction_metadata
from awesome_agent.sandbox.base import CommandRequest, SandboxBackend
from awesome_agent.tools.guardrails import (
    evaluate_command,
    evaluate_file_write,
    is_sensitive_path,
)
from awesome_agent.tools.models import ToolInvocation, ToolResult, ToolSpec
from awesome_agent.tools.registry import ToolRegistry

PUBLIC_READ_TOOL_NAMES = ("ReadFile", "FindFile", "Glob", "Grep")
PUBLIC_WRITE_TOOL_NAMES = ("WriteFile", "EditFile", "Bash")
PUBLIC_MODIFYING_TOOL_NAMES = (
    "ReadFile",
    "FindFile",
    "WriteFile",
    "EditFile",
    "Bash",
    "Glob",
    "Grep",
)
TEXT_FILE_MAX_BYTES = 1_000_000


class WorkspaceToolError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class WorkspacePath:
    requested: str
    relative: Path
    resolved: Path

    @property
    def relative_posix(self) -> str:
        return "." if not self.relative.parts else self.relative.as_posix()


class ReadFileArguments(BaseModel):
    path: str = Field(min_length=1, max_length=500)
    start_line: int = Field(default=1, ge=1)
    end_line: int | None = Field(default=None, ge=1)


class FindFileArguments(BaseModel):
    query: str = Field(min_length=1, max_length=300)
    path: str = Field(default=".", min_length=1, max_length=500)
    max_results: int = Field(default=20, ge=1, le=100)


class WriteFileArguments(BaseModel):
    path: str = Field(min_length=1, max_length=500)
    content: str = Field(max_length=500_000)
    overwrite: bool = False
    create_dirs: bool = False
    allow_empty: bool = False

    @model_validator(mode="after")
    def validate_content(self) -> WriteFileArguments:
        if not self.allow_empty and self.content == "":
            raise ValueError("content must not be empty unless allow_empty is true")
        return self


class EditFileArguments(BaseModel):
    path: str = Field(min_length=1, max_length=500)
    old_text: str = Field(min_length=1, max_length=200_000)
    new_text: str = Field(max_length=200_000)
    expected_replacements: int = Field(default=1, ge=1, le=100)


class BashArguments(BaseModel):
    command: str = Field(min_length=1, max_length=4_000)
    timeout_seconds: float = Field(default=60, gt=0, le=600)
    max_output_chars: int = Field(default=30_000, ge=1_000, le=200_000)


class GlobArguments(BaseModel):
    pattern: str = Field(min_length=1, max_length=300)
    path: str = Field(default=".", min_length=1, max_length=500)
    max_results: int = Field(default=200, ge=1, le=1000)


class GrepArguments(BaseModel):
    pattern: str = Field(min_length=1, max_length=500)
    path: str = Field(default=".", min_length=1, max_length=500)
    include: str | None = Field(default=None, max_length=200)
    regex: bool = False
    case_sensitive: bool = True
    max_results: int = Field(default=100, ge=1, le=500)


def register_public_read_workspace_tools(registry: ToolRegistry) -> None:
    definitions: list[tuple[str, str, RiskLevel, set[str], type[BaseModel], Any]] = [
        (
            "ReadFile",
            "Read text from one workspace file with bounded line ranges.",
            RiskLevel.LOW,
            {"repository:read"},
            ReadFileArguments,
            _read_file,
        ),
        (
            "FindFile",
            (
                "Find workspace files by natural file name, stem, or partial "
                "name. Use before reading or editing when the user mentions an "
                "incomplete file name like 'cube file'."
            ),
            RiskLevel.LOW,
            {"repository:read"},
            FindFileArguments,
            _find_file,
        ),
        (
            "Glob",
            "Find workspace paths by glob pattern without following symlinks.",
            RiskLevel.LOW,
            {"repository:read"},
            GlobArguments,
            _glob,
        ),
        (
            "Grep",
            "Search text files under the workspace with bounded output.",
            RiskLevel.LOW,
            {"repository:read"},
            GrepArguments,
            _grep,
        ),
    ]
    for name, description, risk, capabilities, arguments, handler in definitions:
        registry.register(
            ToolSpec(
                name=name,
                description=description,
                risk_level=risk,
                sandbox_required=False,
                required_capabilities=capabilities,
                input_schema=arguments.model_json_schema(),
            ),
            handler,
        )


def register_public_modifying_workspace_tools(
    registry: ToolRegistry,
    *,
    sandbox: SandboxBackend,
) -> None:
    registry.register(
        ToolSpec(
            name="WriteFile",
            description=(
                "Create or overwrite one UTF-8 workspace file from complete content."
            ),
            risk_level=RiskLevel.MEDIUM,
            sandbox_required=False,
            required_capabilities={"repository:write"},
            input_schema=WriteFileArguments.model_json_schema(),
        ),
        _write_file,
    )
    registry.register(
        ToolSpec(
            name="EditFile",
            description=(
                "Replace exact text inside one UTF-8 workspace file with "
                "ambiguous edits rejected."
            ),
            risk_level=RiskLevel.MEDIUM,
            sandbox_required=False,
            required_capabilities={"repository:write"},
            input_schema=EditFileArguments.model_json_schema(),
        ),
        _edit_file,
    )

    async def bash(invocation: ToolInvocation, progress: object) -> ToolResult:
        return await _bash(invocation, progress, sandbox=sandbox)

    registry.register(
        ToolSpec(
            name="Bash",
            description=("Run one parsed argv command through the configured sandbox."),
            risk_level=RiskLevel.MEDIUM,
            sandbox_required=True,
            required_capabilities={"shell:execute"},
            input_schema=BashArguments.model_json_schema(),
        ),
        bash,
    )


def parse_bash_command(command: str) -> list[str]:
    argv = shlex.split(command, posix=True)
    if not argv:
        raise WorkspaceToolError("Command must not be empty.")
    return argv


async def _read_file(invocation: ToolInvocation, _: object) -> ToolResult:
    arguments = ReadFileArguments.model_validate(invocation.arguments)
    workspace = _workspace(invocation)
    normalized = _safe_existing_path(workspace, arguments.path, expect="file")
    path = normalized.resolved
    if is_sensitive_path(path.relative_to(workspace)):
        raise WorkspaceToolError("Sensitive files cannot be read.")
    text = _read_text_file(path)
    lines = text.splitlines()
    line_count = len(lines)
    if line_count == 0:
        if arguments.start_line != 1:
            raise WorkspaceToolError("start_line is beyond the end of the file.")
        return ToolResult(
            invocation_id=invocation.id,
            output={
                "path": normalized.relative_posix,
                "start_line": 1,
                "end_line": 0,
                "line_count": 0,
                "empty": True,
                "content": "",
            },
        )
    end = arguments.end_line or min(arguments.start_line + 499, line_count)
    if end < arguments.start_line:
        raise WorkspaceToolError("end_line must not precede start_line.")
    if end - arguments.start_line + 1 > 500:
        raise WorkspaceToolError("A read may include at most 500 lines.")
    selected = [
        f"{number}: {lines[number - 1]}"
        for number in range(arguments.start_line, min(end, line_count) + 1)
    ]
    return ToolResult(
        invocation_id=invocation.id,
        output={
            "path": normalized.relative_posix,
            "start_line": arguments.start_line,
            "end_line": min(end, line_count),
            "line_count": line_count,
            "empty": False,
            "content": "\n".join(selected),
        },
    )


async def _write_file(invocation: ToolInvocation, _: object) -> ToolResult:
    arguments = WriteFileArguments.model_validate(invocation.arguments)
    workspace = _workspace(invocation)
    normalized = _safe_workspace_path(workspace, arguments.path)
    relative = normalized.relative
    guardrail = evaluate_file_write(workspace=workspace, paths={relative})
    if guardrail.action == "deny":
        raise WorkspaceToolError(guardrail.reason)
    target = _safe_existing_or_new_path(
        workspace,
        normalized,
        create_dirs=arguments.create_dirs,
    )
    if target.exists() and not arguments.overwrite:
        raise _path_error(
            "File already exists.",
            workspace=workspace,
            requested=normalized.requested,
            resolved=target,
            hint="Pass overwrite=true to replace the file.",
        )
    if target.exists() and not target.is_file():
        raise _path_error(
            "Target path is not a file.",
            workspace=workspace,
            requested=normalized.requested,
            resolved=target,
        )
    before = _file_hash(target)
    before_text = _read_text_file(target) if target.exists() else ""
    _atomic_write_text(target, arguments.content)
    return ToolResult(
        invocation_id=invocation.id,
        output=_write_output(
            status="updated" if before != "<missing>" else "created",
            relative=relative,
            before_hash=before,
            after_hash=_file_hash(target),
            before_text=before_text,
            after_text=arguments.content,
            guardrail={
                "action": guardrail.action,
                "reason": guardrail.reason,
                "name": guardrail.guardrail,
            },
        ),
    )


async def _edit_file(invocation: ToolInvocation, _: object) -> ToolResult:
    arguments = EditFileArguments.model_validate(invocation.arguments)
    workspace = _workspace(invocation)
    normalized = _safe_workspace_path(workspace, arguments.path)
    relative = normalized.relative
    guardrail = evaluate_file_write(workspace=workspace, paths={relative})
    if guardrail.action == "deny":
        raise WorkspaceToolError(guardrail.reason)
    normalized_existing = _safe_existing_path(workspace, arguments.path, expect="file")
    target = normalized_existing.resolved
    before_text = _read_text_file(target)
    occurrences = before_text.count(arguments.old_text)
    if occurrences != arguments.expected_replacements:
        raise _path_error(
            "old_text occurrence count does not match expected_replacements.",
            workspace=workspace,
            requested=normalized_existing.requested,
            resolved=target,
            hint="Run ReadFile first and verify the exact text before editing.",
        )
    after_text = before_text.replace(
        arguments.old_text,
        arguments.new_text,
        arguments.expected_replacements,
    )
    before = _file_hash(target)
    _atomic_write_text(target, after_text)
    return ToolResult(
        invocation_id=invocation.id,
        output=_write_output(
            status="updated",
            relative=relative,
            before_hash=before,
            after_hash=_file_hash(target),
            before_text=before_text,
            after_text=after_text,
            guardrail={
                "action": guardrail.action,
                "reason": guardrail.reason,
                "name": guardrail.guardrail,
            },
        ),
    )


async def _bash(
    invocation: ToolInvocation,
    _: object,
    *,
    sandbox: SandboxBackend,
) -> ToolResult:
    arguments = BashArguments.model_validate(invocation.arguments)
    argv = parse_bash_command(arguments.command)
    guardrail = evaluate_command(argv)
    if guardrail.action == "deny":
        raise WorkspaceToolError(guardrail.reason)
    if guardrail.action == "ask" and not invocation.approval_granted:
        raise WorkspaceToolError(guardrail.reason)
    started = monotonic()
    result = await sandbox.execute(
        CommandRequest(
            argv=argv,
            workspace=_workspace(invocation),
            timeout_seconds=arguments.timeout_seconds,
            max_output_chars=arguments.max_output_chars,
        )
    )
    duration_ms = round((monotonic() - started) * 1000, 3)
    stdout, stdout_truncated = _bound(arguments.max_output_chars, result.stdout)
    stderr, stderr_truncated = _bound(arguments.max_output_chars, result.stderr)
    stdout_redaction = redact_text(stdout)
    stderr_redaction = redact_text(stderr)
    redaction = stdout_redaction.report.merge(stderr_redaction.report)
    return ToolResult(
        invocation_id=invocation.id,
        output={
            "status": "completed" if result.exit_code == 0 else "failed",
            "command": arguments.command,
            "argv": argv,
            "exit_code": result.exit_code,
            "stdout": stdout_redaction.text,
            "stderr": stderr_redaction.text,
            "timed_out": result.timed_out,
            "duration_ms": duration_ms,
            "stdout_truncated": stdout_truncated,
            "stderr_truncated": stderr_truncated,
            "sandbox": result.sandbox or sandbox.name,
            "guardrail": {
                "action": guardrail.action,
                "reason": guardrail.reason,
                "name": guardrail.guardrail,
            },
            "redaction": redaction_metadata(redaction),
        },
    )


async def _find_file(invocation: ToolInvocation, _: object) -> ToolResult:
    arguments = FindFileArguments.model_validate(invocation.arguments)
    workspace = _workspace(invocation)
    normalized_root = _safe_workspace_path(workspace, arguments.path)
    root = normalized_root.resolved
    if not root.exists():
        return ToolResult(
            invocation_id=invocation.id,
            output={
                "matches": [],
                "ambiguous": False,
                "truncated": False,
                "warning": f"Path does not exist: {normalized_root.relative_posix}",
            },
        )
    _validate_existing_workspace_path(workspace, normalized_root)
    paths = [root] if root.is_file() else list(_iter_files(root))
    matches: list[dict[str, object]] = []
    for path in paths:
        relative = path.relative_to(workspace)
        score, reason = _file_match_score(relative, arguments.query)
        if score <= 0:
            continue
        matches.append(
            {
                "path": relative.as_posix(),
                "name": path.name,
                "score": score,
                "reason": reason,
            }
        )
    matches.sort(key=_file_match_sort_key)
    truncated = len(matches) > arguments.max_results
    bounded = matches[: arguments.max_results]
    return ToolResult(
        invocation_id=invocation.id,
        output={
            "query": arguments.query,
            "matches": bounded,
            "ambiguous": len(matches) > 1,
            "truncated": truncated,
        },
    )


async def _glob(invocation: ToolInvocation, _: object) -> ToolResult:
    arguments = GlobArguments.model_validate(invocation.arguments)
    workspace = _workspace(invocation)
    normalized_root = _safe_workspace_path(workspace, arguments.path)
    root = normalized_root.resolved
    if not root.exists():
        return ToolResult(
            invocation_id=invocation.id,
            output={
                "paths": [],
                "truncated": False,
                "warning": f"Path does not exist: {normalized_root.relative_posix}",
            },
        )
    _validate_existing_workspace_path(
        workspace,
        normalized_root,
        expect="directory",
    )
    matches: list[str] = []
    for path in _iter_paths(root):
        relative = path.relative_to(workspace).as_posix()
        if _glob_matches(relative, path.name, arguments.pattern):
            matches.append(relative)
            if len(matches) >= arguments.max_results:
                return ToolResult(
                    invocation_id=invocation.id,
                    output={"paths": matches, "truncated": True},
                )
    return ToolResult(
        invocation_id=invocation.id,
        output={"paths": matches, "truncated": False},
    )


async def _grep(invocation: ToolInvocation, _: object) -> ToolResult:
    arguments = GrepArguments.model_validate(invocation.arguments)
    workspace = _workspace(invocation)
    normalized_root = _safe_workspace_path(workspace, arguments.path)
    root = normalized_root.resolved
    if not root.exists():
        return ToolResult(
            invocation_id=invocation.id,
            output={
                "matches": [],
                "truncated": False,
                "warning": f"Path does not exist: {normalized_root.relative_posix}",
            },
        )
    _validate_existing_workspace_path(workspace, normalized_root)
    paths = [root] if root.is_file() else list(_iter_files(root))
    flags = 0 if arguments.case_sensitive else re.IGNORECASE
    regex = re.compile(arguments.pattern, flags) if arguments.regex else None
    matches: list[dict[str, object]] = []
    for path in paths:
        relative = path.relative_to(workspace)
        if arguments.include and not fnmatch.fnmatch(
            relative.as_posix(),
            arguments.include,
        ):
            continue
        if is_sensitive_path(relative):
            continue
        try:
            text = _read_text_file(path)
        except WorkspaceToolError:
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            found = (
                bool(regex.search(line))
                if regex is not None
                else _line_contains(
                    line,
                    arguments.pattern,
                    case_sensitive=arguments.case_sensitive,
                )
            )
            if found:
                matches.append(
                    {
                        "path": relative.as_posix(),
                        "line": line_number,
                        "text": line[:500],
                    }
                )
                if len(matches) >= arguments.max_results:
                    return ToolResult(
                        invocation_id=invocation.id,
                        output={"matches": matches, "truncated": True},
                    )
    return ToolResult(
        invocation_id=invocation.id,
        output={"matches": matches, "truncated": False},
    )


def _workspace(invocation: ToolInvocation) -> Path:
    if invocation.workspace is None:
        raise WorkspaceToolError("Tool invocation has no Run workspace.")
    return invocation.workspace.resolve()


def _safe_workspace_path(workspace: Path, requested: str) -> WorkspacePath:
    if not requested:
        raise _path_error(
            "Path must not be empty.",
            workspace=workspace,
            requested=requested,
        )
    root = workspace.resolve()
    raw = Path(requested)
    if raw.is_absolute():
        resolved = raw.resolve()
        if resolved != root and not resolved.is_relative_to(root):
            raise _path_error(
                "Path is outside the workspace.",
                workspace=root,
                requested=requested,
                resolved=resolved,
            )
        relative = Path() if resolved == root else resolved.relative_to(root)
        return WorkspacePath(
            requested=requested,
            relative=relative,
            resolved=resolved,
        )
    if ".." in raw.parts or ".git" in raw.parts:
        raise _path_error(
            "Path must remain inside the workspace.",
            workspace=root,
            requested=requested,
            hint="Use a workspace-relative path without '..' or '.git'.",
        )
    relative = Path() if not raw.parts else raw
    resolved = (root / relative).resolve()
    if resolved != root and not resolved.is_relative_to(root):
        raise _path_error(
            "Resolved path escapes the workspace.",
            workspace=root,
            requested=requested,
            resolved=resolved,
        )
    return WorkspacePath(
        requested=requested,
        relative=relative,
        resolved=resolved,
    )


def _safe_existing_path(
    workspace: Path,
    relative: str,
    *,
    expect: str | None = None,
) -> WorkspacePath:
    normalized = _safe_workspace_path(workspace, relative)
    if not normalized.resolved.exists():
        raise _path_error(
            f"Path does not exist: {normalized.relative_posix}",
            workspace=workspace,
            requested=normalized.requested,
            resolved=normalized.resolved,
            hint=f"Use workspace-relative path: {normalized.relative_posix}",
        )
    _validate_existing_workspace_path(workspace, normalized, expect=expect)
    return normalized


def _validate_existing_workspace_path(
    workspace: Path,
    normalized: WorkspacePath,
    *,
    expect: str | None = None,
) -> None:
    root = workspace.resolve()
    candidate = normalized.resolved
    if _contains_symlink(root, candidate):
        raise _path_error(
            "Symlink or junction paths are not allowed.",
            workspace=root,
            requested=normalized.requested,
            resolved=candidate,
        )
    if expect == "file" and not candidate.is_file():
        raise _path_error(
            "Path is not a file.",
            workspace=root,
            requested=normalized.requested,
            resolved=candidate,
        )
    if expect == "directory" and not candidate.is_dir():
        raise _path_error(
            "Path is not a directory.",
            workspace=root,
            requested=normalized.requested,
            resolved=candidate,
        )


def _safe_existing_or_new_path(
    workspace: Path,
    normalized: WorkspacePath,
    *,
    create_dirs: bool,
) -> Path:
    root = workspace.resolve()
    candidate = normalized.resolved
    if candidate != root and not candidate.is_relative_to(root):
        raise _path_error(
            "Resolved path escapes the workspace.",
            workspace=root,
            requested=normalized.requested,
            resolved=candidate,
        )
    parent = candidate.parent
    if not parent.exists():
        if not create_dirs:
            raise _path_error(
                "Parent directory does not exist.",
                workspace=root,
                requested=normalized.requested,
                resolved=candidate,
                hint="Pass create_dirs=true to create parent directories.",
            )
        nearest = _nearest_existing_parent(root, parent)
        if _contains_symlink(root, nearest):
            raise _path_error(
                "Symlink or junction paths are not allowed.",
                workspace=root,
                requested=normalized.requested,
                resolved=nearest,
            )
        parent.mkdir(parents=True, exist_ok=True)
    if _contains_symlink(root, parent):
        raise _path_error(
            "Symlink or junction paths are not allowed.",
            workspace=root,
            requested=normalized.requested,
            resolved=parent,
        )
    if candidate.exists() and _contains_symlink(root, candidate):
        raise _path_error(
            "Symlink or junction paths are not allowed.",
            workspace=root,
            requested=normalized.requested,
            resolved=candidate,
        )
    return candidate


def _nearest_existing_parent(root: Path, path: Path) -> Path:
    current = path
    while not current.exists():
        if current == root or not current.is_relative_to(root):
            raise _path_error(
                "Path parent escapes the workspace.",
                workspace=root,
                requested=str(path),
                resolved=current,
            )
        current = current.parent
    return current


def _path_error(
    message: str,
    *,
    workspace: Path,
    requested: str,
    resolved: Path | None = None,
    hint: str | None = None,
) -> WorkspaceToolError:
    payload: dict[str, str] = {
        "error": message,
        "workspace": str(workspace),
        "requested_path": requested,
    }
    if resolved is not None:
        payload["resolved_path"] = str(resolved)
    if hint:
        payload["hint"] = hint
    return WorkspaceToolError(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _contains_symlink(root: Path, candidate: Path) -> bool:
    current = root
    for part in candidate.relative_to(root).parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def _read_text_file(path: Path) -> str:
    data = path.read_bytes()
    if len(data) > TEXT_FILE_MAX_BYTES:
        raise WorkspaceToolError("Text file is too large to read.")
    for marker, encoding in (
        (b"\xff\xfe", "utf-16"),
        (b"\xfe\xff", "utf-16"),
        (b"\xef\xbb\xbf", "utf-8-sig"),
    ):
        if data.startswith(marker):
            try:
                return data.decode(encoding)
            except UnicodeDecodeError as error:
                raise WorkspaceToolError(
                    "Binary or unsupported text encoding files cannot be read."
                ) from error
    if b"\x00" in data:
        raise WorkspaceToolError(
            "Binary or unsupported text encoding files cannot be read."
        )
    for encoding in ("utf-8", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise WorkspaceToolError(
        "Binary or unsupported text encoding files cannot be read."
    )


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        delete=False,
        newline="",
    ) as handle:
        handle.write(content)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def _file_hash(path: Path) -> str:
    if not path.exists():
        return "<missing>"
    return _sha256(path.read_bytes())


def _sha256(content: bytes) -> str:
    from hashlib import sha256

    return sha256(content).hexdigest()


def _write_output(
    *,
    status: str,
    relative: Path,
    before_hash: str,
    after_hash: str,
    before_text: str,
    after_text: str,
    guardrail: dict[str, str],
) -> dict[str, Any]:
    path = relative.as_posix()
    return {
        "status": status,
        "path": path,
        "paths": [path],
        "preimage_hashes": {path: before_hash},
        "postimage_hashes": {path: after_hash},
        "change_stats": _change_stats(before_text, after_text),
        "guardrail": guardrail,
    }


def _change_stats(before: str, after: str) -> dict[str, int]:
    before_lines = before.splitlines()
    after_lines = after.splitlines()
    return {
        "bytes_before": len(before.encode("utf-8")),
        "bytes_after": len(after.encode("utf-8")),
        "lines_before": len(before_lines),
        "lines_after": len(after_lines),
        "line_delta": len(after_lines) - len(before_lines),
    }


def _iter_paths(root: Path) -> Iterable[Path]:
    if root.is_file():
        yield root
        return
    for current, directories, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        directories[:] = [
            name
            for name in sorted(directories)
            if name != ".git" and not (current_path / name).is_symlink()
        ]
        for name in sorted(directories):
            path = current_path / name
            if not path.is_symlink():
                yield path
        for name in sorted(files):
            path = current_path / name
            if not path.is_symlink():
                yield path


def _iter_files(root: Path) -> Iterable[Path]:
    for path in _iter_paths(root):
        if path.is_file():
            yield path


def _glob_matches(relative: str, name: str, pattern: str) -> bool:
    normalized = pattern.replace("\\", "/")
    for variant in _glob_pattern_variants(normalized):
        if fnmatch.fnmatch(relative, variant) or fnmatch.fnmatch(name, variant):
            return True
    return False


def _glob_pattern_variants(pattern: str) -> set[str]:
    variants = {pattern}
    queue = [pattern]
    while queue:
        current = queue.pop()
        candidates: list[str] = []
        if current.startswith("**/"):
            candidates.append(current[3:])
        marker = "/**/"
        start = current.find(marker)
        while start != -1:
            candidates.append(current[: start + 1] + current[start + len(marker) :])
            start = current.find(marker, start + 1)
        for candidate in candidates:
            if candidate and candidate not in variants:
                variants.add(candidate)
                queue.append(candidate)
    return variants


def _file_match_score(relative: Path, query: str) -> tuple[int, str]:
    name = relative.name.casefold()
    stem = relative.stem.casefold()
    relative_text = relative.as_posix().casefold()
    for term in _file_query_terms(query):
        if name == term:
            return 110, "filename exact match"
        if stem == term:
            return 100, "stem exact match"
        if relative_text == term:
            return 95, "path exact match"
        if name.startswith(term):
            return 90, "filename prefix match"
        if stem.startswith(term):
            return 85, "stem prefix match"
        if term in name:
            return 70, "filename contains query"
        if term in relative_text:
            return 60, "path contains query"
    return 0, ""


def _file_match_sort_key(match: dict[str, object]) -> tuple[int, str]:
    score = match.get("score")
    return (-(score if isinstance(score, int) else 0), str(match.get("path", "")))


def _file_query_terms(query: str) -> list[str]:
    cleaned = query.casefold().strip().strip("\"'`")
    for word in (
        "files",
        "file",
        "named",
        "called",
        "path",
    ):
        cleaned = re.sub(rf"\b{re.escape(word)}\b", " ", cleaned)
    for phrase in (
        "文件",
        "内容",
        "介绍",
        "修改",
        "读取",
        "读",
        "的",
    ):
        cleaned = cleaned.replace(phrase, " ")
    candidates = [cleaned.strip()]
    candidates.extend(
        part.strip("\"'`.,:;()[]{}<>") for part in re.split(r"[\s/\\]+", cleaned)
    )
    candidates.extend(re.findall(r"[a-z0-9][a-z0-9_.-]*", cleaned))
    terms: list[str] = []
    for candidate in candidates:
        if candidate and candidate not in terms:
            terms.append(candidate)
    return terms


def _line_contains(line: str, pattern: str, *, case_sensitive: bool) -> bool:
    if case_sensitive:
        return pattern in line
    return pattern.lower() in line.lower()


def _bound(limit: int, value: str) -> tuple[str, bool]:
    if len(value) <= limit:
        return value, False
    return value[:limit], True
