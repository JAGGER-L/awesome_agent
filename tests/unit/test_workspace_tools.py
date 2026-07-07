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

    assert names == {"ReadFile", "FindFile", "Glob", "Grep"}


def test_public_modifying_registry_definitions() -> None:
    names = {
        definition.name
        for definition in model_tool_definitions(build_modifying_registry())
    }

    assert names == {
        "ReadFile",
        "FindFile",
        "WriteFile",
        "EditFile",
        "Bash",
        "Glob",
        "Grep",
    }


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
async def test_readfile_empty_utf8_file_returns_empty_success(tmp_path: Path) -> None:
    (tmp_path / "cube.py").write_text("", encoding="utf-8")

    result = await _read_call(tmp_path, "ReadFile", {"path": "cube.py"})

    assert not result.is_error
    payload = json.loads(result.content)
    assert payload["path"] == "cube.py"
    assert payload["start_line"] == 1
    assert payload["end_line"] == 0
    assert payload["line_count"] == 0
    assert payload["empty"] is True
    assert payload["content"] == ""


@pytest.mark.asyncio
async def test_readfile_empty_utf16_bom_file_returns_empty_success(
    tmp_path: Path,
) -> None:
    (tmp_path / "cube.py").write_bytes(b"\xff\xfe")

    result = await _read_call(tmp_path, "ReadFile", {"path": "cube.py"})

    assert not result.is_error
    payload = json.loads(result.content)
    assert payload["path"] == "cube.py"
    assert payload["start_line"] == 1
    assert payload["end_line"] == 0
    assert payload["line_count"] == 0
    assert payload["empty"] is True
    assert payload["content"] == ""


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
async def test_glob_and_grep_default_to_workspace_root(tmp_path: Path) -> None:
    (tmp_path / "snake.html").write_text("<h1>snake</h1>\n", encoding="utf-8")

    default_glob = await _read_call(tmp_path, "Glob", {"pattern": "*.html"})
    dot_glob = await _read_call(
        tmp_path,
        "Glob",
        {"pattern": "*.html", "path": "."},
    )
    default_grep = await _read_call(tmp_path, "Grep", {"pattern": "snake"})
    dot_grep = await _read_call(
        tmp_path,
        "Grep",
        {"pattern": "snake", "path": "."},
    )

    assert not default_glob.is_error
    assert not dot_glob.is_error
    assert '"snake.html"' in default_glob.content
    assert '"snake.html"' in dot_glob.content
    assert not default_grep.is_error
    assert not dot_grep.is_error
    assert '"path": "snake.html"' in default_grep.content
    assert '"path": "snake.html"' in dot_grep.content


@pytest.mark.asyncio
async def test_glob_globstar_matches_workspace_root_files(tmp_path: Path) -> None:
    (tmp_path / "cube.py").write_text("print('cube')\n", encoding="utf-8")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "cube_nested.py").write_text(
        "print('nested')\n",
        encoding="utf-8",
    )

    all_files = await _read_call(tmp_path, "Glob", {"pattern": "**/*"})
    py_files = await _read_call(tmp_path, "Glob", {"pattern": "**/*.py"})
    cube_files = await _read_call(tmp_path, "Glob", {"pattern": "**/*cube*"})

    for result in (all_files, py_files, cube_files):
        assert not result.is_error
        assert '"cube.py"' in result.content
        assert '"nested/cube_nested.py"' in result.content


@pytest.mark.asyncio
async def test_find_file_finds_workspace_root_file_by_stem(tmp_path: Path) -> None:
    (tmp_path / "cube.py").write_text("print('cube')\n", encoding="utf-8")
    (tmp_path / "snake.html").write_text("<h1>snake</h1>\n", encoding="utf-8")

    result = await _read_call(tmp_path, "FindFile", {"query": "cube"})

    assert not result.is_error
    assert '"path": "cube.py"' in result.content
    assert '"score": 100' in result.content
    assert '"ambiguous": false' in result.content


@pytest.mark.asyncio
async def test_find_file_accepts_natural_file_phrase(tmp_path: Path) -> None:
    (tmp_path / "cube.py").write_text("print('cube')\n", encoding="utf-8")

    result = await _read_call(tmp_path, "FindFile", {"query": "cube file"})

    assert not result.is_error
    assert '"path": "cube.py"' in result.content
    assert '"ambiguous": false' in result.content


@pytest.mark.asyncio
async def test_find_file_does_not_strip_file_substring_from_names(
    tmp_path: Path,
) -> None:
    (tmp_path / "profile.py").write_text("PROFILE = True\n", encoding="utf-8")

    result = await _read_call(tmp_path, "FindFile", {"query": "profile"})

    assert not result.is_error
    assert '"path": "profile.py"' in result.content
    assert '"score": 100' in result.content


@pytest.mark.asyncio
async def test_find_file_reports_ambiguous_matches(tmp_path: Path) -> None:
    (tmp_path / "cube.py").write_text("print('cube')\n", encoding="utf-8")
    (tmp_path / "cube_test.py").write_text("print('test')\n", encoding="utf-8")

    result = await _read_call(tmp_path, "FindFile", {"query": "cube"})

    assert not result.is_error
    assert '"cube.py"' in result.content
    assert '"cube_test.py"' in result.content
    assert '"ambiguous": true' in result.content


@pytest.mark.asyncio
async def test_read_file_accepts_utf16_bom_text(tmp_path: Path) -> None:
    (tmp_path / "cube.py").write_text(
        "def cube(value):\n    return value ** 3\n",
        encoding="utf-16",
    )

    result = await _read_call(tmp_path, "ReadFile", {"path": "cube.py"})

    assert not result.is_error
    assert "1: def cube(value):" in result.content
    assert "2:     return value ** 3" in result.content


@pytest.mark.asyncio
async def test_find_read_write_flow_can_update_named_file(tmp_path: Path) -> None:
    (tmp_path / "cube.py").write_text("old = True\n", encoding="utf-8")

    found = await _read_call(tmp_path, "FindFile", {"query": "cube"})
    read = await _read_call(tmp_path, "ReadFile", {"path": "cube.py"})
    written = await _write_call(
        tmp_path,
        "WriteFile",
        {
            "path": "cube.py",
            "content": "def cube(value: int) -> int:\n    return value ** 3\n",
            "overwrite": True,
        },
    )

    assert not found.is_error
    assert '"path": "cube.py"' in found.content
    assert not read.is_error
    assert "old = True" in read.content
    assert not written.is_error
    assert (tmp_path / "cube.py").read_text(encoding="utf-8") == (
        "def cube(value: int) -> int:\n    return value ** 3\n"
    )


@pytest.mark.asyncio
async def test_workspace_tools_accept_absolute_paths_inside_workspace(
    tmp_path: Path,
) -> None:
    target = tmp_path / "nested" / "snake.html"
    target.parent.mkdir()
    target.write_text("<h1>snake</h1>\n", encoding="utf-8")

    read = await _read_call(tmp_path, "ReadFile", {"path": str(target)})
    globbed = await _read_call(
        tmp_path,
        "Glob",
        {"pattern": "*.html", "path": str(target.parent)},
    )

    assert not read.is_error
    assert '"path": "nested/snake.html"' in read.content
    assert not globbed.is_error
    assert '"nested/snake.html"' in globbed.content


@pytest.mark.asyncio
async def test_workspace_tools_reject_absolute_paths_outside_workspace(
    tmp_path: Path,
) -> None:
    outside = tmp_path.parent / "outside-workspace.txt"
    outside.write_text("outside\n", encoding="utf-8")

    result = await _read_call(tmp_path, "ReadFile", {"path": str(outside)})

    assert result.is_error
    assert "outside the workspace" in result.content
    assert "requested_path" in result.content
    assert "Use workspace-relative path" not in result.content


@pytest.mark.asyncio
async def test_glob_and_grep_missing_search_root_returns_warning(
    tmp_path: Path,
) -> None:
    globbed = await _read_call(
        tmp_path,
        "Glob",
        {"pattern": "*.py", "path": "missing"},
    )
    grepped = await _read_call(
        tmp_path,
        "Grep",
        {"pattern": "needle", "path": "missing"},
    )

    assert not globbed.is_error
    assert '"paths": []' in globbed.content
    assert '"warning": "Path does not exist: missing"' in globbed.content
    assert not grepped.is_error
    assert '"matches": []' in grepped.content
    assert '"warning": "Path does not exist: missing"' in grepped.content


@pytest.mark.asyncio
async def test_workspace_file_errors_include_actionable_hints(
    tmp_path: Path,
) -> None:
    (tmp_path / "existing.py").write_text("alpha\n", encoding="utf-8")

    missing_read = await _read_call(tmp_path, "ReadFile", {"path": "missing.py"})
    missing_parent_write = await _write_call(
        tmp_path,
        "WriteFile",
        {"path": "new/app.py", "content": "print('ok')\n"},
    )
    existing_write = await _write_call(
        tmp_path,
        "WriteFile",
        {"path": "existing.py", "content": "beta\n"},
    )
    missing_old_text = await _write_call(
        tmp_path,
        "EditFile",
        {"path": "existing.py", "old_text": "absent", "new_text": "beta"},
    )

    assert missing_read.is_error
    assert "requested_path" in missing_read.content
    assert "workspace" in missing_read.content
    assert "Use workspace-relative path: missing.py" in missing_read.content
    assert missing_parent_write.is_error
    assert "create_dirs=true" in missing_parent_write.content
    assert existing_write.is_error
    assert "overwrite=true" in existing_write.content
    assert missing_old_text.is_error
    assert "ReadFile" in missing_old_text.content
    assert "exact text" in missing_old_text.content


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
                arguments_json=json.dumps({"path": ".env", "content": "TOKEN=value\n"}),
            ),
            workspace=tmp_path,
            agent_id=uuid4(),
            capabilities={"repository:read", "repository:write"},
        )
