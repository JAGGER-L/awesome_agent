from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from awesome_agent.modeling import ToolCall
from awesome_agent.sandbox.base import CommandRequest, CommandResult
from awesome_agent.tools.approval import ApprovalPolicy
from awesome_agent.tools.executor import ToolExecutor
from awesome_agent.tools.models import ApprovalRequired
from awesome_agent.tools.repository import (
    build_modifying_executor,
    build_modifying_registry,
    build_read_only_executor,
    build_read_only_registry,
    execute_repository_call,
    model_tool_definitions,
)


class RecordingSandbox:
    name = "recording"

    def __init__(self) -> None:
        self.requests: list[CommandRequest] = []

    async def execute(self, request: CommandRequest) -> CommandResult:
        self.requests.append(request)
        return CommandResult(
            command=request.command_label,
            exit_code=0,
            stdout="ok\n",
            stderr="",
            sandbox=self.name,
        )


async def _read_call(
    workspace: Path,
    name: str,
    arguments: dict[str, object],
):
    registry = build_read_only_registry()
    return await execute_repository_call(
        build_read_only_executor(registry),
        ToolCall(
            call_id=f"call-{name}",
            name=name,
            arguments_json=json.dumps(arguments),
        ),
        workspace=workspace,
        agent_id=uuid4(),
    )


async def _write_call(
    workspace: Path,
    name: str,
    arguments: dict[str, object],
    *,
    approval_granted: bool = False,
    sandbox: RecordingSandbox | None = None,
):
    registry = build_modifying_registry(sandbox=sandbox or RecordingSandbox())
    return await execute_repository_call(
        build_modifying_executor(registry),
        ToolCall(
            call_id=f"call-{name}",
            name=name,
            arguments_json=json.dumps(arguments),
        ),
        workspace=workspace,
        agent_id=uuid4(),
        capabilities={"repository:read", "repository:write", "shell:execute"},
        approval_granted=approval_granted,
    )


def test_public_read_registry_definitions() -> None:
    names = {
        definition.name
        for definition in model_tool_definitions(build_read_only_registry())
    }

    assert names == {"ReadFile", "Glob", "Grep"}


def test_public_modifying_registry_definitions() -> None:
    names = {
        definition.name
        for definition in model_tool_definitions(build_modifying_registry())
    }

    assert names == {"ReadFile", "WriteFile", "EditFile", "Bash", "Glob", "Grep"}


@pytest.mark.asyncio
async def test_readfile_reads_bounded_text(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("one\ntwo\nthree\n", encoding="utf-8")

    result = await _read_call(
        tmp_path,
        "ReadFile",
        {"path": "app.py", "start_line": 2, "end_line": 3},
    )

    assert not result.is_error
    assert "2: two" in result.content
    assert "3: three" in result.content


@pytest.mark.asyncio
async def test_readfile_rejects_escape_sensitive_and_binary(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("TOKEN=value\n", encoding="utf-8")
    (tmp_path / "binary.bin").write_bytes(b"\x00\x01")

    escaped = await _read_call(tmp_path, "ReadFile", {"path": "../secret"})
    sensitive = await _read_call(tmp_path, "ReadFile", {"path": ".env"})
    binary = await _read_call(tmp_path, "ReadFile", {"path": "binary.bin"})

    assert escaped.is_error
    assert sensitive.is_error
    assert binary.is_error


@pytest.mark.asyncio
async def test_writefile_writes_complete_content(tmp_path: Path) -> None:
    result = await _write_call(
        tmp_path,
        "WriteFile",
        {"path": "new.py", "content": "print('ok')\n"},
    )

    assert not result.is_error
    assert (tmp_path / "new.py").read_text(encoding="utf-8") == "print('ok')\n"
    payload = json.loads(result.content)
    assert payload["paths"] == ["new.py"]
    assert payload["preimage_hashes"]["new.py"] == "<missing>"
    assert payload["postimage_hashes"]["new.py"] != "<missing>"
    assert payload["change_stats"]["lines_after"] == 1


@pytest.mark.asyncio
async def test_writefile_rejects_empty_and_accidental_overwrite(
    tmp_path: Path,
) -> None:
    (tmp_path / "existing.py").write_text("old\n", encoding="utf-8")

    empty = await _write_call(
        tmp_path,
        "WriteFile",
        {"path": "empty.py", "content": ""},
    )
    overwrite = await _write_call(
        tmp_path,
        "WriteFile",
        {"path": "existing.py", "content": "new\n"},
    )

    assert empty.is_error
    assert overwrite.is_error
    assert (tmp_path / "existing.py").read_text(encoding="utf-8") == "old\n"


@pytest.mark.asyncio
async def test_editfile_replaces_exact_text(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("alpha\nbeta\n", encoding="utf-8", newline="")

    result = await _write_call(
        tmp_path,
        "EditFile",
        {"path": "app.py", "old_text": "beta\n", "new_text": "gamma\n"},
    )

    assert not result.is_error
    assert (tmp_path / "app.py").read_text(encoding="utf-8") == "alpha\ngamma\n"


@pytest.mark.asyncio
async def test_editfile_rejects_missing_or_ambiguous_text(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("same\nsame\n", encoding="utf-8", newline="")

    missing = await _write_call(
        tmp_path,
        "EditFile",
        {"path": "app.py", "old_text": "absent", "new_text": "x"},
    )
    ambiguous = await _write_call(
        tmp_path,
        "EditFile",
        {"path": "app.py", "old_text": "same", "new_text": "x"},
    )

    assert missing.is_error
    assert ambiguous.is_error


@pytest.mark.asyncio
async def test_glob_and_grep_find_workspace_content(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("needle\n", encoding="utf-8")

    globbed = await _read_call(tmp_path, "Glob", {"pattern": "*.py", "path": "src"})
    grepped = await _read_call(tmp_path, "Grep", {"pattern": "needle", "path": "src"})

    assert "src/app.py" in globbed.content
    assert '"line": 1' in grepped.content


@pytest.mark.asyncio
async def test_bash_runs_command_through_sandbox(tmp_path: Path) -> None:
    sandbox = RecordingSandbox()

    result = await _write_call(
        tmp_path,
        "Bash",
        {"command": "pytest -q"},
        sandbox=sandbox,
    )

    assert not result.is_error
    assert sandbox.requests[0].argv == ["pytest", "-q"]
    assert '"duration_ms"' in result.content


@pytest.mark.asyncio
async def test_writefile_sensitive_path_requires_approval(tmp_path: Path) -> None:
    registry = build_modifying_registry()
    executor = ToolExecutor(registry, ApprovalPolicy())

    with pytest.raises(ApprovalRequired):
        await execute_repository_call(
            executor,
            ToolCall(
                call_id="sensitive",
                name="WriteFile",
                arguments_json=json.dumps(
                    {"path": ".env", "content": "TOKEN=value\n"}
                ),
            ),
            workspace=tmp_path,
            agent_id=uuid4(),
            capabilities={"repository:read", "repository:write"},
        )
