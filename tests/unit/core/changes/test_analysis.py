from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from awesome_agent.core.changes import (
    BinaryFileChange,
    ChangeAnalysis,
    ChangeAnalyzer,
    ChangeLifecycle,
    ChangeOperations,
    ChangeReversibility,
    ChangeSet,
    DirectoryChange,
    FileChange,
    FileChangeKind,
    FileNodeType,
    SymlinkChange,
    TextFileChange,
    merge_file_changes,
)
from awesome_agent.core.changes.errors import ChangeBlobCorrupt
from awesome_agent.core.workspace import resolve_workspace
from awesome_agent.storage.changes import FileChangeBlobStore, SQLiteChangeSetStore


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _stored_change(
    *,
    path: str,
    kind: FileChangeKind,
    node_type: FileNodeType,
    before: bytes | None,
    after: bytes | None,
    blobs: FileChangeBlobStore,
) -> FileChange:
    return FileChange(
        path=path,
        kind=kind,
        node_type=node_type,
        before_hash=_digest(before) if before is not None else None,
        after_hash=_digest(after) if after is not None else None,
        before_blob=(
            blobs.put(before)
            if before is not None and node_type is not FileNodeType.DIRECTORY
            else None
        ),
        after_blob=(
            blobs.put(after)
            if after is not None and node_type is not FileNodeType.DIRECTORY
            else None
        ),
    )


def _fixture(
    tmp_path: Path,
) -> tuple[ChangeAnalyzer, ChangeOperations, SQLiteChangeSetStore, FileChangeBlobStore]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    identity = resolve_workspace(workspace)
    store = SQLiteChangeSetStore(tmp_path / "application.db")
    blobs = FileChangeBlobStore(tmp_path / "change-journal")
    analyzer = ChangeAnalyzer(store, blobs, identity)
    operations = ChangeOperations(store, blobs, identity, analyzer=analyzer)
    return analyzer, operations, store, blobs


@pytest.mark.parametrize(
    ("before_type", "after_type", "before_content", "after_content"),
    [
        (FileNodeType.DIRECTORY, FileNodeType.FILE, b"", b"file"),
        (FileNodeType.SYMLINK, FileNodeType.FILE, b"old-target", b"file"),
        (FileNodeType.FILE, FileNodeType.DIRECTORY, b"file", b""),
        (FileNodeType.FILE, FileNodeType.SYMLINK, b"file", b"new-target"),
    ],
)
def test_merge_preserves_distinct_before_and_after_node_types(
    tmp_path: Path,
    before_type: FileNodeType,
    after_type: FileNodeType,
    before_content: bytes,
    after_content: bytes,
) -> None:
    blobs = FileChangeBlobStore(tmp_path / "change-journal")
    deleted = FileChange(
        path="node",
        kind=FileChangeKind.DELETED,
        node_type=before_type,
        before_node_type=before_type,
        after_node_type=None,
        before_hash=_digest(before_content),
        before_blob=(
            None if before_type is FileNodeType.DIRECTORY else blobs.put(before_content)
        ),
    )
    created = FileChange(
        path="node",
        kind=FileChangeKind.CREATED,
        node_type=after_type,
        before_node_type=None,
        after_node_type=after_type,
        after_hash=_digest(after_content),
        after_blob=(
            None if after_type is FileNodeType.DIRECTORY else blobs.put(after_content)
        ),
    )

    merged = merge_file_changes([deleted, created])

    assert len(merged) == 1
    assert merged[0].before_node_type is before_type
    assert merged[0].after_node_type is after_type


def test_analysis_returns_text_counts_and_the_same_unified_diff(
    tmp_path: Path,
) -> None:
    analyzer, operations, store, blobs = _fixture(tmp_path)
    before = b"def area(r):\n    return 0\n"
    after = b"def area(r):\n    return 3.14 * r * r\n"
    change_set = ChangeSet(
        id="change_text",
        session_id="session_1",
        turn_id="turn_1",
        workspace_key=resolve_workspace(tmp_path / "workspace").key,
        lifecycle=ChangeLifecycle.APPLIED,
        reversibility=ChangeReversibility.FULL,
        files=[
            _stored_change(
                path="area.py",
                kind=FileChangeKind.UPDATED,
                node_type=FileNodeType.FILE,
                before=before,
                after=after,
                blobs=blobs,
            )
        ],
        created_at=datetime.now(UTC),
        sealed_at=datetime.now(UTC),
    )
    store.save(change_set)

    analysis = analyzer.analyze(change_set.id)

    assert analysis == ChangeAnalysis(
        diff=analysis.diff,
        changes=(
            TextFileChange(
                path="area.py",
                change_kind=FileChangeKind.UPDATED,
                additions=1,
                deletions=1,
            ),
        ),
    )
    assert "--- a/area.py" in analysis.diff
    assert "+++ b/area.py" in analysis.diff
    assert "-    return 0" in analysis.diff
    assert "+    return 3.14 * r * r" in analysis.diff
    assert operations.diff(change_set.id) == analysis.diff


def test_analysis_classifies_binary_directory_and_symlink(tmp_path: Path) -> None:
    analyzer, _, store, blobs = _fixture(tmp_path)
    change_set = ChangeSet(
        id="change_mixed",
        session_id="session_1",
        turn_id="turn_1",
        workspace_key=resolve_workspace(tmp_path / "workspace").key,
        lifecycle=ChangeLifecycle.APPLIED,
        reversibility=ChangeReversibility.FULL,
        files=[
            _stored_change(
                path="asset.bin",
                kind=FileChangeKind.UPDATED,
                node_type=FileNodeType.FILE,
                before=b"before\x00",
                after=b"after\x00bytes",
                blobs=blobs,
            ),
            _stored_change(
                path="generated",
                kind=FileChangeKind.CREATED,
                node_type=FileNodeType.DIRECTORY,
                before=None,
                after=b"",
                blobs=blobs,
            ),
            _stored_change(
                path="current",
                kind=FileChangeKind.UPDATED,
                node_type=FileNodeType.SYMLINK,
                before=b"release-1",
                after=b"release-2",
                blobs=blobs,
            ),
        ],
        created_at=datetime.now(UTC),
        sealed_at=datetime.now(UTC),
    )
    store.save(change_set)

    analysis = analyzer.analyze(change_set.id)

    assert analysis.changes == (
        BinaryFileChange(
            path="asset.bin",
            change_kind=FileChangeKind.UPDATED,
            before_bytes=7,
            after_bytes=11,
        ),
        SymlinkChange(
            path="current",
            change_kind=FileChangeKind.UPDATED,
        ),
        DirectoryChange(
            path="generated",
            change_kind=FileChangeKind.CREATED,
        ),
    )
    assert "Binary change: asset.bin (7 -> 11 bytes)" in analysis.diff
    assert "Symlink change: current" in analysis.diff
    assert "Directory change: generated" in analysis.diff


def test_analysis_of_execute_only_change_set_is_empty(tmp_path: Path) -> None:
    analyzer, _, store, _ = _fixture(tmp_path)
    change_set = ChangeSet(
        id="change_execute",
        session_id="session_1",
        turn_id=None,
        workspace_key=resolve_workspace(tmp_path / "workspace").key,
        lifecycle=ChangeLifecycle.APPLIED,
        reversibility=ChangeReversibility.NONE,
        created_at=datetime.now(UTC),
        sealed_at=datetime.now(UTC),
    )
    store.save(change_set)

    assert analyzer.analyze(change_set.id) == ChangeAnalysis()


def test_analysis_rejects_a_blob_that_disagrees_with_the_recorded_hash(
    tmp_path: Path,
) -> None:
    analyzer, _, store, blobs = _fixture(tmp_path)
    content = b"actual content\n"
    blob = blobs.put(content)
    change_set = ChangeSet(
        id="change_corrupt",
        session_id="session_1",
        turn_id="turn_1",
        workspace_key=resolve_workspace(tmp_path / "workspace").key,
        lifecycle=ChangeLifecycle.APPLIED,
        reversibility=ChangeReversibility.FULL,
        files=[
            FileChange(
                path="corrupt.txt",
                kind=FileChangeKind.CREATED,
                node_type=FileNodeType.FILE,
                after_hash=_digest(b"different content\n"),
                after_blob=blob,
            )
        ],
        created_at=datetime.now(UTC),
        sealed_at=datetime.now(UTC),
    )
    store.save(change_set)

    with pytest.raises(ChangeBlobCorrupt, match="does not match"):
        analyzer.analyze(change_set.id)
