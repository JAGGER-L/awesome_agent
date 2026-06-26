from __future__ import annotations

import json
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest

from awesome_agent.modeling import ToolCall, ToolResultMessage
from awesome_agent.tools.repository import (
    RepositoryRecoveryRequired,
    build_modifying_executor,
    build_modifying_registry,
    build_read_only_executor,
    build_read_only_registry,
    execute_repository_call,
    model_tool_definitions,
)


def _git(path: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=path,
        capture_output=True,
        check=True,
        text=True,
    )
    return result.stdout.strip()


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


async def _modifying_call(
    workspace: Path,
    name: str,
    arguments: dict[str, object],
) -> ToolResultMessage:
    registry = build_modifying_registry()
    return await execute_repository_call(
        build_modifying_executor(registry),
        ToolCall(
            call_id=f"call-{name}",
            name=name,
            arguments_json=json.dumps(arguments),
        ),
        workspace=workspace,
        agent_id=uuid4(),
        capabilities={"repository:read", "repository:write"},
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


@pytest.mark.asyncio
async def test_apply_patch_and_diff_report_changed_file(tmp_path: Path) -> None:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "README.md").write_text("old\n", encoding="utf-8")
    _git(tmp_path, "add", "README.md")
    _git(tmp_path, "commit", "-m", "Initial")

    patch = """diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -1 +1 @@
-old
+new
"""

    applied = await _modifying_call(tmp_path, "repo.apply_patch", {"patch": patch})
    diff = await _modifying_call(tmp_path, "repo.diff", {})

    assert not applied.is_error
    assert "README.md" in applied.content
    assert "new" in (tmp_path / "README.md").read_text(encoding="utf-8")
    assert '"changed": true' in diff.content
    assert "+new" in diff.content


@pytest.mark.asyncio
async def test_apply_patch_rejects_git_and_parent_paths(tmp_path: Path) -> None:
    _git(tmp_path, "init")
    git_patch = """diff --git a/.git/config b/.git/config
--- a/.git/config
+++ b/.git/config
@@ -1 +1 @@
-old
+new
"""
    parent_patch = """diff --git a/../outside.txt b/../outside.txt
--- a/../outside.txt
+++ b/../outside.txt
@@ -1 +1 @@
-old
+new
"""

    git_result = await _modifying_call(
        tmp_path,
        "repo.apply_patch",
        {"patch": git_patch},
    )
    parent_result = await _modifying_call(
        tmp_path,
        "repo.apply_patch",
        {"patch": parent_patch},
    )

    assert git_result.is_error
    assert parent_result.is_error


@pytest.mark.asyncio
async def test_apply_patch_requires_write_capability(tmp_path: Path) -> None:
    _git(tmp_path, "init")
    registry = build_modifying_registry()

    result = await execute_repository_call(
        build_modifying_executor(registry),
        ToolCall(
            call_id="write-denied",
            name="repo.apply_patch",
            arguments_json='{"patch":"--- a/file.txt\\n+++ b/file.txt\\n"}',
        ),
        workspace=tmp_path,
        agent_id=uuid4(),
        capabilities={"repository:read"},
    )

    assert result.is_error
    assert "ToolDenied" in result.content


@pytest.mark.asyncio
async def test_status_and_instructions_tools_report_repository_context(
    tmp_path: Path,
) -> None:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "README.md").write_text("readme\n", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("agent instructions\n", encoding="utf-8")
    _git(tmp_path, "add", "README.md", "AGENTS.md")
    _git(tmp_path, "commit", "-m", "Initial")

    status = await _call(tmp_path, "repo.status", {})
    instructions = await _call(tmp_path, "repo.instructions", {"path": "README.md"})

    assert '"clean": true' in status.content
    assert "agent instructions" in instructions.content
    assert "README.md" in instructions.content


@pytest.mark.asyncio
async def test_diff_truncates_large_output(tmp_path: Path) -> None:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "large.txt").write_text("old\n", encoding="utf-8")
    _git(tmp_path, "add", "large.txt")
    _git(tmp_path, "commit", "-m", "Initial")
    (tmp_path / "large.txt").write_text("new\n" * 200, encoding="utf-8")

    diff = await _modifying_call(
        tmp_path,
        "repo.diff",
        {"max_chars": 1_000, "context_lines": 20},
    )

    assert not diff.is_error
    assert '"truncated": true' in diff.content


@pytest.mark.asyncio
async def test_apply_patch_reports_check_failure(tmp_path: Path) -> None:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "README.md").write_text("actual\n", encoding="utf-8")
    _git(tmp_path, "add", "README.md")
    _git(tmp_path, "commit", "-m", "Initial")
    patch = """diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -1 +1 @@
-expected
+new
"""

    with pytest.raises(RepositoryRecoveryRequired):
        await _modifying_call(tmp_path, "repo.apply_patch", {"patch": patch})


@pytest.mark.asyncio
async def test_apply_patch_treats_existing_postimage_as_completed(
    tmp_path: Path,
) -> None:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "README.md").write_text("old\n", encoding="utf-8")
    _git(tmp_path, "add", "README.md")
    _git(tmp_path, "commit", "-m", "Initial")
    patch = """diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -1 +1 @@
-old
+new
"""

    first = await _modifying_call(tmp_path, "repo.apply_patch", {"patch": patch})
    second = await _modifying_call(tmp_path, "repo.apply_patch", {"patch": patch})

    assert not first.is_error
    assert not second.is_error
    assert '"status": "already_applied"' in second.content
