import hashlib
from pathlib import Path

import pytest

from awesome_agent.memory.local_file import (
    LocalMemoryFile,
    MemoryDocumentInvalid,
    render_memory_document,
)
from awesome_agent.memory.models import MemoryMutationStatus, MemoryScope

START = "<!-- awesome-agent:managed-memory:start -->"
END = "<!-- awesome-agent:managed-memory:end -->"
FIRST_ID = "memory_11111111111111111111111111111111"
SECOND_ID = "memory_22222222222222222222222222222222"


def test_absent_file_is_empty_snapshot_without_creation(tmp_path: Path) -> None:
    path = tmp_path / "USER.md"
    memory = LocalMemoryFile(path=path, scope=MemoryScope.USER)

    document = memory.snapshot()

    assert document.markdown == ""
    assert document.entries == ()
    assert document.content_hash == hashlib.sha256(b"").hexdigest()
    assert path.exists() is False


def test_round_trip_and_crud_preserve_free_markdown_bytes(tmp_path: Path) -> None:
    path = tmp_path / "MEMORY.md"
    prefix = "# 人工笔记\r\n\r\n保留此处。\r\n"
    managed = (
        f"{START}\r\n<!-- memory:id={FIRST_ID} -->\r\n- Existing fact\r\n{END}\r\n"
    )
    suffix = "\r\n## 尾注\r\n不要改。\r\n"
    original = (prefix + managed + suffix).encode("utf-8")
    path.write_bytes(original)
    ids = iter((SECOND_ID,))
    memory = LocalMemoryFile(
        path=path,
        scope=MemoryScope.WORKSPACE,
        id_factory=lambda: next(ids),
    )

    snapshot = memory.snapshot()
    assert render_memory_document(snapshot) == original
    assert [entry.id for entry in snapshot.entries] == [FIRST_ID]

    added = memory.add("Second fact", expected_hash=snapshot.content_hash)
    assert added.status is MemoryMutationStatus.ADDED
    assert added.entry_id == SECOND_ID
    after_add = path.read_bytes()
    assert after_add.startswith(prefix.encode("utf-8"))
    assert after_add.endswith(suffix.encode("utf-8"))

    assert added.document is not None
    replaced = memory.replace(
        FIRST_ID,
        "Updated fact",
        expected_hash=added.document.content_hash,
    )
    assert replaced.status is MemoryMutationStatus.REPLACED
    assert replaced.document is not None
    assert [entry.content for entry in replaced.document.entries] == [
        "Updated fact",
        "Second fact",
    ]

    removed = memory.remove(
        SECOND_ID,
        expected_hash=replaced.document.content_hash,
    )
    assert removed.status is MemoryMutationStatus.REMOVED
    assert path.read_bytes().startswith(prefix.encode("utf-8"))
    assert path.read_bytes().endswith(suffix.encode("utf-8"))


@pytest.mark.parametrize(
    "content",
    [
        f"{START}\n",
        f"{END}\n",
        f"{END}\n{START}\n",
        f"{START}\n{START}\n{END}\n",
        f"{START}\ntext without id\n{END}\n",
        f"{START}\n<!-- memory:id=bad -->\n- fact\n{END}\n",
    ],
)
def test_malformed_managed_sections_are_rejected(
    tmp_path: Path,
    content: str,
) -> None:
    path = tmp_path / "USER.md"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(MemoryDocumentInvalid):
        LocalMemoryFile(path=path, scope=MemoryScope.USER).snapshot()


def test_duplicate_ids_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "USER.md"
    path.write_text(
        f"{START}\n"
        f"<!-- memory:id={FIRST_ID} -->\n- first\n"
        f"<!-- memory:id={FIRST_ID} -->\n- second\n"
        f"{END}\n",
        encoding="utf-8",
    )

    with pytest.raises(MemoryDocumentInvalid):
        LocalMemoryFile(path=path, scope=MemoryScope.USER).snapshot()


def test_compare_hash_conflict_never_overwrites_manual_edit(tmp_path: Path) -> None:
    path = tmp_path / "USER.md"
    memory = LocalMemoryFile(
        path=path,
        scope=MemoryScope.USER,
        id_factory=lambda: FIRST_ID,
    )
    observed = memory.snapshot()
    path.write_text("manual edit", encoding="utf-8")

    result = memory.add("fact", expected_hash=observed.content_hash)

    assert result.status is MemoryMutationStatus.CONFLICT
    assert path.read_text(encoding="utf-8") == "manual edit"


def test_utf8_and_document_size_bounds_are_enforced(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.md"
    invalid.write_bytes(b"\xff")
    with pytest.raises(MemoryDocumentInvalid):
        LocalMemoryFile(path=invalid, scope=MemoryScope.USER).snapshot()

    large = tmp_path / "large.md"
    large.write_bytes("界".encode() * 10)
    with pytest.raises(MemoryDocumentInvalid):
        LocalMemoryFile(
            path=large,
            scope=MemoryScope.USER,
            max_document_bytes=20,
        ).snapshot()


def test_failed_atomic_replace_keeps_original_and_cleans_temporary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "USER.md"
    path.write_text("original", encoding="utf-8")
    memory = LocalMemoryFile(
        path=path,
        scope=MemoryScope.USER,
        id_factory=lambda: FIRST_ID,
    )
    observed = memory.snapshot()

    def fail_replace(source: Path | str, destination: Path | str) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr("awesome_agent.memory.local_file.os.replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        memory.add("fact", expected_hash=observed.content_hash)

    assert path.read_text(encoding="utf-8") == "original"
    assert list(tmp_path.glob("*.tmp")) == []


def test_invalid_candidate_section_is_rejected_before_replacement(
    tmp_path: Path,
) -> None:
    path = tmp_path / "USER.md"
    path.write_text("manual notes", encoding="utf-8")
    memory = LocalMemoryFile(
        path=path,
        scope=MemoryScope.USER,
        id_factory=lambda: FIRST_ID,
    )
    observed = memory.snapshot()

    with pytest.raises(MemoryDocumentInvalid):
        memory.add(START, expected_hash=observed.content_hash)

    assert path.read_text(encoding="utf-8") == "manual notes"
