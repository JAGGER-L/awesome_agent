from __future__ import annotations

import fnmatch
import os
import re
import shlex
import tempfile
from collections.abc import Iterable
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

PUBLIC_READ_TOOL_NAMES = ("ReadFile", "Glob", "Grep")
PUBLIC_WRITE_TOOL_NAMES = ("WriteFile", "EditFile", "Bash")
PUBLIC_MODIFYING_TOOL_NAMES = (
    "ReadFile",
    "WriteFile",
    "EditFile",
    "Bash",
    "Glob",
    "Grep",
)
TEXT_FILE_MAX_BYTES = 1_000_000


class WorkspaceToolError(RuntimeError):
    pass


class ReadFileArguments(BaseModel):
    path: str = Field(min_length=1, max_length=500)
    start_line: int = Field(default=1, ge=1)
    end_line: int | None = Field(default=None, ge=1)


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
            "Read UTF-8 text from one workspace file with bounded line ranges.",
            RiskLevel.LOW,
            {"repository:read"},
            ReadFileArguments,
            _read_file,
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
                "Create or overwrite one UTF-8 workspace file from complete "
                "content."
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
            description=(
                "Run one parsed argv command through the configured sandbox."
            ),
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
    path = _safe_existing_path(workspace, arguments.path, expect="file")
    if is_sensitive_path(path.relative_to(workspace)):
        raise WorkspaceToolError("Sensitive files cannot be read.")
    text = _read_text_file(path)
    lines = text.splitlines()
    end = arguments.end_line or min(arguments.start_line + 499, len(lines))
    if end < arguments.start_line:
        raise WorkspaceToolError("end_line must not precede start_line.")
    if end - arguments.start_line + 1 > 500:
        raise WorkspaceToolError("A read may include at most 500 lines.")
    selected = [
        f"{number}: {lines[number - 1]}"
        for number in range(arguments.start_line, min(end, len(lines)) + 1)
    ]
    return ToolResult(
        invocation_id=invocation.id,
        output={
            "path": path.relative_to(workspace).as_posix(),
            "start_line": arguments.start_line,
            "end_line": min(end, len(lines)),
            "content": "\n".join(selected),
        },
    )


async def _write_file(invocation: ToolInvocation, _: object) -> ToolResult:
    arguments = WriteFileArguments.model_validate(invocation.arguments)
    workspace = _workspace(invocation)
    relative = _safe_relative_path(arguments.path)
    guardrail = evaluate_file_write(workspace=workspace, paths={relative})
    if guardrail.action == "deny":
        raise WorkspaceToolError(guardrail.reason)
    target = _safe_existing_or_new_path(
        workspace,
        relative,
        create_dirs=arguments.create_dirs,
    )
    if target.exists() and not arguments.overwrite:
        raise WorkspaceToolError("File already exists; pass overwrite=true to replace.")
    if target.exists() and not target.is_file():
        raise WorkspaceToolError("Target path is not a file.")
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
    relative = _safe_relative_path(arguments.path)
    guardrail = evaluate_file_write(workspace=workspace, paths={relative})
    if guardrail.action == "deny":
        raise WorkspaceToolError(guardrail.reason)
    target = _safe_existing_path(workspace, arguments.path, expect="file")
    before_text = _read_text_file(target)
    occurrences = before_text.count(arguments.old_text)
    if occurrences != arguments.expected_replacements:
        raise WorkspaceToolError(
            "old_text occurrence count does not match expected_replacements."
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


async def _glob(invocation: ToolInvocation, _: object) -> ToolResult:
    arguments = GlobArguments.model_validate(invocation.arguments)
    workspace = _workspace(invocation)
    root = _safe_existing_path(workspace, arguments.path, expect="directory")
    matches: list[str] = []
    for path in _iter_paths(root):
        relative = path.relative_to(workspace).as_posix()
        if fnmatch.fnmatch(relative, arguments.pattern) or fnmatch.fnmatch(
            path.name,
            arguments.pattern,
        ):
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
    root = _safe_existing_path(workspace, arguments.path)
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


def _safe_relative_path(relative: str) -> Path:
    raw = Path(relative)
    if raw.is_absolute() or ".." in raw.parts or ".git" in raw.parts:
        raise WorkspaceToolError("Path must remain inside the Run worktree.")
    if not raw.parts:
        raise WorkspaceToolError("Path must not be empty.")
    return raw


def _safe_existing_path(
    workspace: Path,
    relative: str,
    *,
    expect: str | None = None,
) -> Path:
    raw = _safe_relative_path(relative)
    root = workspace.resolve()
    candidate = (root / raw).resolve()
    if candidate != root and not candidate.is_relative_to(root):
        raise WorkspaceToolError("Resolved path escapes the Run worktree.")
    if not candidate.exists():
        raise WorkspaceToolError(f"Path does not exist: {relative}")
    if _contains_symlink(root, candidate):
        raise WorkspaceToolError("Symlink or junction paths are not allowed.")
    if expect == "file" and not candidate.is_file():
        raise WorkspaceToolError("Path is not a file.")
    if expect == "directory" and not candidate.is_dir():
        raise WorkspaceToolError("Path is not a directory.")
    return candidate


def _safe_existing_or_new_path(
    workspace: Path,
    relative: Path,
    *,
    create_dirs: bool,
) -> Path:
    root = workspace.resolve()
    candidate = (root / relative).resolve()
    if candidate != root and not candidate.is_relative_to(root):
        raise WorkspaceToolError("Resolved path escapes the Run worktree.")
    parent = candidate.parent
    if not parent.exists():
        if not create_dirs:
            raise WorkspaceToolError("Parent directory does not exist.")
        nearest = _nearest_existing_parent(root, parent)
        if _contains_symlink(root, nearest):
            raise WorkspaceToolError("Symlink or junction paths are not allowed.")
        parent.mkdir(parents=True, exist_ok=True)
    if _contains_symlink(root, parent):
        raise WorkspaceToolError("Symlink or junction paths are not allowed.")
    if candidate.exists() and _contains_symlink(root, candidate):
        raise WorkspaceToolError("Symlink or junction paths are not allowed.")
    return candidate


def _nearest_existing_parent(root: Path, path: Path) -> Path:
    current = path
    while not current.exists():
        if current == root or not current.is_relative_to(root):
            raise WorkspaceToolError("Path parent escapes the Run worktree.")
        current = current.parent
    return current


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
    if b"\x00" in data:
        raise WorkspaceToolError("Binary or non-UTF-8 files cannot be read.")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise WorkspaceToolError("Binary or non-UTF-8 files cannot be read.") from error


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


def _line_contains(line: str, pattern: str, *, case_sensitive: bool) -> bool:
    if case_sensitive:
        return pattern in line
    return pattern.lower() in line.lower()


def _bound(limit: int, value: str) -> tuple[str, bool]:
    if len(value) <= limit:
        return value, False
    return value[:limit], True
