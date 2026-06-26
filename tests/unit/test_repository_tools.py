from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from awesome_agent.modeling import ToolCall, ToolResultMessage
from awesome_agent.tools.repository import (
    build_read_only_executor,
    build_read_only_registry,
    execute_repository_call,
    model_tool_definitions,
)


async def _call(
    workspace: Path,
    name: str,
    arguments: dict[str, object],
) -> ToolResultMessage:
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


@pytest.mark.asyncio
async def test_list_search_and_read_are_bounded_to_workspace(
    tmp_path: Path,
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text(
        "first\nneedle\nthird\n",
        encoding="utf-8",
    )

    listing = await _call(tmp_path, "repo.list", {"path": "src"})
    search = await _call(tmp_path, "repo.search", {"query": "needle"})
    read = await _call(tmp_path, "repo.read", {"path": "src/app.py"})

    assert "src/app.py" in listing.content
    assert '"line": 2' in search.content
    assert "2: needle" in read.content


@pytest.mark.asyncio
async def test_read_rejects_escape_sensitive_and_binary_files(
    tmp_path: Path,
) -> None:
    (tmp_path / ".env").write_text("SECRET=value", encoding="utf-8")
    (tmp_path / "binary.bin").write_bytes(b"\x00\x01")

    escaped = await _call(tmp_path, "repo.read", {"path": "../secret"})
    sensitive = await _call(tmp_path, "repo.read", {"path": ".env"})
    binary = await _call(tmp_path, "repo.read", {"path": "binary.bin"})

    assert escaped.is_error
    assert sensitive.is_error
    assert binary.is_error


@pytest.mark.asyncio
async def test_malformed_arguments_return_correctable_tool_error(
    tmp_path: Path,
) -> None:
    registry = build_read_only_registry()
    result = await execute_repository_call(
        build_read_only_executor(registry),
        ToolCall(
            call_id="bad",
            name="repo.read",
            arguments_json='{"path":',
        ),
        workspace=tmp_path,
        agent_id=uuid4(),
    )

    assert result.is_error
    assert "valid JSON" in result.content


@pytest.mark.asyncio
async def test_repository_calls_pass_through_executor_capability_policy(
    tmp_path: Path,
) -> None:
    (tmp_path / "README.md").write_text("fixture\n", encoding="utf-8")
    registry = build_read_only_registry()
    read_spec, _ = registry.resolve("repo.read")
    read_spec.required_capabilities = {"repository:write"}

    result = await execute_repository_call(
        build_read_only_executor(registry),
        ToolCall(
            call_id="denied",
            name="repo.read",
            arguments_json='{"path":"README.md"}',
        ),
        workspace=tmp_path,
        agent_id=uuid4(),
    )

    assert result.is_error
    assert "ToolDenied" in result.content


def test_registry_exposes_model_json_schemas() -> None:
    definitions = model_tool_definitions(build_read_only_registry())

    assert {definition.name for definition in definitions} == {
        "repo.status",
        "repo.list",
        "repo.search",
        "repo.read",
        "repo.instructions",
    }
