from pathlib import Path

import pytest

from awesome_agent.memory import (
    LocalMemoryService,
    MemoryMutationStatus,
    MemoryScope,
)
from awesome_agent.paths import AwesomePaths


def test_paths_use_exact_home_and_opaque_workspace_key(tmp_path: Path) -> None:
    paths = AwesomePaths.from_home(tmp_path / "awesome-home")

    assert paths.user_memory_file == tmp_path / "awesome-home" / "memory" / "USER.md"
    assert paths.workspace_memory_file("ws_abc123") == (
        tmp_path / "awesome-home" / "workspaces" / "ws_abc123" / "MEMORY.md"
    )
    with pytest.raises(ValueError):
        paths.workspace_memory_file("../repository-name")


def test_default_disabled_status_and_reads_create_no_files(tmp_path: Path) -> None:
    paths = AwesomePaths.from_home(tmp_path / "home")
    service = LocalMemoryService(
        paths=paths,
        workspace_key="ws_123",
    )

    status = service.status()
    user = service.snapshot(MemoryScope.USER)
    workspace = service.snapshot(MemoryScope.WORKSPACE)

    assert status.enabled is False
    assert {item.label for item in status.scopes} == {
        "memory/USER.md",
        "workspaces/ws_123/MEMORY.md",
    }
    assert all(item.exists is False for item in status.scopes)
    assert user.markdown == workspace.markdown == ""
    assert service.list(MemoryScope.USER) == ()
    assert paths.user_memory_file.exists() is False
    assert paths.workspace_memory_file("ws_123").exists() is False
    assert str(tmp_path) not in status.model_dump_json()


def test_disabled_mutations_are_visible_results_and_do_not_delete_files(
    tmp_path: Path,
) -> None:
    paths = AwesomePaths.from_home(tmp_path / "home")
    paths.user_memory_file.parent.mkdir(parents=True)
    paths.user_memory_file.write_text("manual", encoding="utf-8")
    service = LocalMemoryService(paths=paths, workspace_key="ws_123")
    observed = service.snapshot(MemoryScope.USER)

    result = service.add(
        MemoryScope.USER,
        "Prefer short answers.",
        expected_hash=observed.content_hash,
    )

    assert result.status is MemoryMutationStatus.DISABLED
    assert paths.user_memory_file.read_text(encoding="utf-8") == "manual"


def test_enabled_crud_is_policy_checked_and_scope_isolated(tmp_path: Path) -> None:
    paths = AwesomePaths.from_home(tmp_path / "home")
    service = LocalMemoryService(
        paths=paths,
        workspace_key="ws_123",
        enabled=True,
        id_factory=lambda: "memory_11111111111111111111111111111111",
    )
    user_hash = service.snapshot(MemoryScope.USER).content_hash
    workspace_hash = service.snapshot(MemoryScope.WORKSPACE).content_hash

    added = service.add(
        MemoryScope.USER,
        "  Prefer concise answers.  ",
        expected_hash=user_hash,
    )
    assert added.status is MemoryMutationStatus.ADDED
    assert added.document is not None
    assert added.document.entries[0].content == "Prefer concise answers."

    cross_scope = service.remove(
        MemoryScope.WORKSPACE,
        added.entry_id or "",
        expected_hash=workspace_hash,
    )
    assert cross_scope.status is MemoryMutationStatus.NOT_FOUND
    assert len(service.list(MemoryScope.USER)) == 1

    rejected = service.add(
        MemoryScope.WORKSPACE,
        "token=super-secret-value",
        expected_hash=workspace_hash,
    )
    assert rejected.status is MemoryMutationStatus.REJECTED
    assert "super-secret-value" not in rejected.model_dump_json()


def test_enabled_status_counts_existing_entries_without_external_state(
    tmp_path: Path,
) -> None:
    service = LocalMemoryService(
        paths=AwesomePaths.from_home(tmp_path / "home"),
        workspace_key="ws_opaque",
        enabled=True,
        id_factory=lambda: "memory_22222222222222222222222222222222",
    )
    observed = service.snapshot(MemoryScope.WORKSPACE)
    service.add(
        MemoryScope.WORKSPACE,
        "Project uses pytest.",
        expected_hash=observed.content_hash,
    )

    status = service.status()

    assert status.enabled is True
    workspace = next(
        item for item in status.scopes if item.scope is MemoryScope.WORKSPACE
    )
    assert workspace.entry_count == 1
    assert workspace.exists is True
