from __future__ import annotations

import json
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from awesome_agent.domain.enums import RiskLevel
from awesome_agent.modeling import ToolCall, ToolDefinition, ToolResultMessage
from awesome_agent.sandbox.process import run_process
from awesome_agent.tools.approval import ApprovalPolicy
from awesome_agent.tools.executor import ToolExecutor
from awesome_agent.tools.models import ToolInvocation, ToolResult, ToolSpec
from awesome_agent.tools.registry import ToolRegistry

TOOL_RESULT_MAX_CHARS = 30_000
_SENSITIVE_NAMES = {
    ".env",
    ".env.local",
    ".npmrc",
    ".pypirc",
    "credentials",
    "credentials.json",
    "id_dsa",
    "id_ed25519",
    "id_rsa",
}
_SENSITIVE_SUFFIXES = {".key", ".pem", ".p12", ".pfx"}


class RepositoryToolError(RuntimeError):
    pass


class StatusArguments(BaseModel):
    pass


class ListArguments(BaseModel):
    path: str = "."
    max_depth: int = Field(default=3, ge=0, le=10)
    max_entries: int = Field(default=500, ge=1, le=1000)


class SearchArguments(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    path: str = "."
    file_glob: str | None = Field(default=None, max_length=200)
    max_results: int = Field(default=100, ge=1, le=300)


class ReadArguments(BaseModel):
    path: str
    start_line: int = Field(default=1, ge=1)
    end_line: int | None = Field(default=None, ge=1)


class InstructionsArguments(BaseModel):
    path: str = "."


_ARGUMENT_MODELS: dict[str, type[BaseModel]] = {
    "repo.status": StatusArguments,
    "repo.list": ListArguments,
    "repo.search": SearchArguments,
    "repo.read": ReadArguments,
    "repo.instructions": InstructionsArguments,
}


def build_read_only_registry() -> ToolRegistry:
    registry = ToolRegistry()
    definitions = [
        (
            "repo.status",
            "Inspect the Run worktree Git revision and status.",
            _status,
        ),
        (
            "repo.list",
            "List bounded repository entries without following symlinks.",
            _list,
        ),
        (
            "repo.search",
            "Search literal text in bounded repository files.",
            _search,
        ),
        (
            "repo.read",
            "Read a bounded line range from one repository text file.",
            _read,
        ),
        (
            "repo.instructions",
            "Discover applicable AGENTS.md, README, and project metadata.",
            _instructions,
        ),
    ]
    for name, description, handler in definitions:
        arguments = _ARGUMENT_MODELS[name]
        registry.register(
            ToolSpec(
                name=name,
                description=description,
                risk_level=RiskLevel.LOW,
                sandbox_required=False,
                required_capabilities={"repository:read"},
                input_schema=arguments.model_json_schema(),
            ),
            handler,
        )
    return registry


def model_tool_definitions(registry: ToolRegistry) -> list[ToolDefinition]:
    return [
        ToolDefinition(
            name=spec.name,
            description=spec.description,
            input_schema=spec.input_schema,
        )
        for spec in registry.list_specs()
    ]


async def execute_repository_call(
    executor: ToolExecutor,
    call: ToolCall,
    *,
    workspace: Path,
    agent_id: Any,
    profile: str = "leader",
) -> ToolResultMessage:
    try:
        arguments = _parse_arguments(call)
        result = await executor.execute(
            ToolInvocation(
                id=_tool_uuid(call.call_id),
                tool_name=call.name,
                agent_id=agent_id,
                profile=profile,
                capabilities={"repository:read"},
                arguments=arguments,
                workspace=workspace,
            ),
            progress=None,
        )
        return ToolResultMessage(
            call_id=call.call_id,
            content=_bounded_json(result.output),
        )
    except (
        RepositoryToolError,
        ValidationError,
        ValueError,
        KeyError,
        TimeoutError,
        RuntimeError,
    ) as error:
        return ToolResultMessage(
            call_id=call.call_id,
            content=f"{type(error).__name__}: {error}",
            is_error=True,
        )


def build_read_only_executor(registry: ToolRegistry | None = None) -> ToolExecutor:
    return ToolExecutor(registry or build_read_only_registry(), ApprovalPolicy())


def _parse_arguments(call: ToolCall) -> dict[str, Any]:
    model = _ARGUMENT_MODELS.get(call.name)
    if model is None:
        raise RepositoryToolError(f"Unknown read-only tool: {call.name}")
    try:
        raw = json.loads(call.arguments_json)
    except json.JSONDecodeError as error:
        raise RepositoryToolError(
            f"Arguments are not valid JSON: {error.msg}"
        ) from error
    if not isinstance(raw, dict):
        raise RepositoryToolError("Tool arguments must be a JSON object.")
    return model.model_validate(raw).model_dump(mode="json")


async def _status(
    invocation: ToolInvocation,
    _: object,
) -> ToolResult:
    workspace = _workspace(invocation)
    revision = await _git(workspace, "rev-parse", "HEAD")
    branch = await _git(workspace, "branch", "--show-current")
    status = await _git(workspace, "status", "--short", "--untracked-files=all")
    return ToolResult(
        invocation_id=invocation.id,
        output={
            "revision": revision.strip(),
            "branch": branch.strip(),
            "clean": not status.strip(),
            "changes": status.splitlines(),
        },
    )


async def _list(invocation: ToolInvocation, _: object) -> ToolResult:
    arguments = ListArguments.model_validate(invocation.arguments)
    root = _safe_path(_workspace(invocation), arguments.path, expect="directory")
    base = _workspace(invocation)
    entries: list[dict[str, object]] = []
    start_depth = len(root.relative_to(base).parts)
    for current, directories, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        depth = len(current_path.relative_to(base).parts) - start_depth
        directories[:] = sorted(
            name
            for name in directories
            if name != ".git" and not (current_path / name).is_symlink()
        )
        if depth >= arguments.max_depth:
            directories[:] = []
        for name, kind in [
            *((name, "directory") for name in directories),
            *((name, "file") for name in sorted(files)),
        ]:
            path = current_path / name
            if path.is_symlink():
                continue
            entries.append(
                {
                    "path": path.relative_to(base).as_posix(),
                    "kind": kind,
                }
            )
            if len(entries) >= arguments.max_entries:
                return ToolResult(
                    invocation_id=invocation.id,
                    output={"entries": entries, "truncated": True},
                )
    return ToolResult(
        invocation_id=invocation.id,
        output={"entries": entries, "truncated": False},
    )


async def _search(invocation: ToolInvocation, _: object) -> ToolResult:
    arguments = SearchArguments.model_validate(invocation.arguments)
    root = _safe_path(_workspace(invocation), arguments.path, expect="directory")
    base = _workspace(invocation)
    matches: list[dict[str, object]] = []
    pattern = arguments.file_glob or "*"
    for path in _iter_files(root):
        if not path.match(pattern) or _is_sensitive(path):
            continue
        text = _read_text(path)
        if text is None:
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            if arguments.query in line:
                matches.append(
                    {
                        "path": path.relative_to(base).as_posix(),
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


async def _read(invocation: ToolInvocation, _: object) -> ToolResult:
    arguments = ReadArguments.model_validate(invocation.arguments)
    path = _safe_path(_workspace(invocation), arguments.path, expect="file")
    if _is_sensitive(path):
        raise RepositoryToolError("Sensitive files cannot be read.")
    text = _read_text(path)
    if text is None:
        raise RepositoryToolError("Binary or non-UTF-8 files cannot be read.")
    lines = text.splitlines()
    end = arguments.end_line or min(arguments.start_line + 499, len(lines))
    if end < arguments.start_line:
        raise RepositoryToolError("end_line must not precede start_line.")
    if end - arguments.start_line + 1 > 500:
        raise RepositoryToolError("A read may include at most 500 lines.")
    selected = [
        f"{number}: {lines[number - 1]}"
        for number in range(arguments.start_line, min(end, len(lines)) + 1)
    ]
    return ToolResult(
        invocation_id=invocation.id,
        output={
            "path": path.relative_to(_workspace(invocation)).as_posix(),
            "start_line": arguments.start_line,
            "end_line": min(end, len(lines)),
            "content": "\n".join(selected),
        },
    )


async def _instructions(invocation: ToolInvocation, _: object) -> ToolResult:
    arguments = InstructionsArguments.model_validate(invocation.arguments)
    workspace = _workspace(invocation)
    target = _safe_path(workspace, arguments.path)
    directory = target if target.is_dir() else target.parent
    candidates: list[Path] = []
    current = directory
    while current == workspace or current.is_relative_to(workspace):
        candidates.extend(
            path
            for name in ("AGENTS.md", "README.md", "README.rst", "pyproject.toml")
            if (path := current / name).is_file() and not path.is_symlink()
        )
        if current == workspace:
            break
        current = current.parent
    documents: list[dict[str, str]] = []
    for path in dict.fromkeys(reversed(candidates)):
        text = _read_text(path)
        if text is not None:
            documents.append(
                {
                    "path": path.relative_to(workspace).as_posix(),
                    "content": text[:10_000],
                }
            )
    return ToolResult(
        invocation_id=invocation.id,
        output={"documents": documents},
    )


def _safe_path(
    workspace: Path,
    relative: str,
    *,
    expect: Literal["file", "directory"] | None = None,
) -> Path:
    raw = Path(relative)
    if raw.is_absolute() or ".." in raw.parts or ".git" in raw.parts:
        raise RepositoryToolError("Path must remain inside the Run worktree.")
    root = workspace.resolve()
    candidate = (root / raw).resolve()
    if candidate != root and not candidate.is_relative_to(root):
        raise RepositoryToolError("Resolved path escapes the Run worktree.")
    if not candidate.exists():
        raise RepositoryToolError(f"Path does not exist: {relative}")
    if _contains_symlink(root, candidate):
        raise RepositoryToolError("Symlink or junction paths are not allowed.")
    if expect == "file" and not candidate.is_file():
        raise RepositoryToolError("Path is not a file.")
    if expect == "directory" and not candidate.is_dir():
        raise RepositoryToolError("Path is not a directory.")
    return candidate


def _contains_symlink(root: Path, candidate: Path) -> bool:
    current = root
    for part in candidate.relative_to(root).parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def _iter_files(root: Path) -> Iterable[Path]:
    for current, directories, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        directories[:] = [
            name
            for name in directories
            if name != ".git" and not (current_path / name).is_symlink()
        ]
        for name in sorted(files):
            path = current_path / name
            if not path.is_symlink():
                yield path


def _read_text(path: Path) -> str | None:
    try:
        data = path.read_bytes()
    except OSError as error:
        raise RepositoryToolError(str(error)) from error
    if b"\x00" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _is_sensitive(path: Path) -> bool:
    return (
        path.name.lower() in _SENSITIVE_NAMES
        or path.suffix.lower() in _SENSITIVE_SUFFIXES
    )


async def _git(workspace: Path, *arguments: str) -> str:
    result = await run_process(
        ["git", *arguments],
        command_label=f"git {' '.join(arguments)}",
        workspace=workspace,
        timeout_seconds=30,
    )
    if result.exit_code != 0:
        raise RepositoryToolError(result.stderr or result.stdout)
    return result.stdout


def _workspace(invocation: ToolInvocation) -> Path:
    if invocation.workspace is None:
        raise RepositoryToolError("Tool invocation has no Run workspace.")
    return invocation.workspace.resolve()


def _bounded_json(value: dict[str, Any]) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    if len(text) <= TOOL_RESULT_MAX_CHARS:
        return text
    head = text[:8000]
    tail = text[-3000:]
    return f"{head}\n...[tool output truncated: {len(text)} characters]...\n{tail}"


def _tool_uuid(call_id: str) -> Any:
    from uuid import NAMESPACE_URL, uuid5

    return uuid5(NAMESPACE_URL, f"awesome-agent:tool-call:{call_id}")
