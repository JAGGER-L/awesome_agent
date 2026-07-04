from pathlib import Path

from awesome_agent.memory.builtin import BuiltinMemoryStore
from awesome_agent.memory.models import MemoryAddRequest, MemoryTarget
from awesome_agent.memory.policy import MemoryPolicy


def test_store_creates_and_lists_structured_entries(tmp_path: Path) -> None:
    store = BuiltinMemoryStore(root=tmp_path, policy=MemoryPolicy())

    result = store.add(
        MemoryAddRequest(
            target=MemoryTarget.USER,
            content="Prefer concise engineering updates.",
            source="explicit_user_request",
        )
    )

    assert result.status == "added"
    entries = store.list_entries(MemoryTarget.USER)
    assert entries == [result.entry]
    assert (
        (tmp_path / "USER.md")
        .read_text(encoding="utf-8")
        .startswith("# User Memory\n\n- [mem_")
    )


def test_delete_only_removes_structured_bullet(tmp_path: Path) -> None:
    store = BuiltinMemoryStore(root=tmp_path, policy=MemoryPolicy())
    result = store.add(
        MemoryAddRequest(
            target=MemoryTarget.MEMORY,
            content="Run targeted tests after runtime changes.",
            source="explicit_user_request",
        )
    )
    assert result.entry is not None
    path = tmp_path / "MEMORY.md"
    path.write_text(
        "manual free text\n" + path.read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    deleted = store.delete(MemoryTarget.MEMORY, result.entry.id)

    assert deleted.status == "deleted"
    assert "manual free text" in path.read_text(encoding="utf-8")
    assert result.entry.id not in path.read_text(encoding="utf-8")


def test_store_lazy_reloads_when_file_changes(tmp_path: Path) -> None:
    store = BuiltinMemoryStore(root=tmp_path, policy=MemoryPolicy())
    store.ensure_files()
    (tmp_path / "USER.md").write_text(
        "# User Memory\n\n- [mem_manual] User prefers examples.\n",
        encoding="utf-8",
    )

    assert store.list_entries(MemoryTarget.USER)[0].content == "User prefers examples."


def test_add_preserves_manual_free_text_without_structured_entries(
    tmp_path: Path,
) -> None:
    store = BuiltinMemoryStore(root=tmp_path, policy=MemoryPolicy())
    store.ensure_files()
    path = tmp_path / "USER.md"
    path.write_text(
        "# User Memory\n\nManual note kept by the user.\n",
        encoding="utf-8",
    )

    result = store.add(
        MemoryAddRequest(
            target=MemoryTarget.USER,
            content="Prefer direct answers.",
            source="explicit_user_request",
        )
    )

    assert result.status == "added"
    text = path.read_text(encoding="utf-8")
    assert "Manual note kept by the user." in text
    assert "- [mem_" in text


def test_default_empty_files_are_not_injected(tmp_path: Path) -> None:
    store = BuiltinMemoryStore(root=tmp_path, policy=MemoryPolicy())
    store.ensure_files()

    snapshot = store.context_snapshot()

    assert snapshot.enabled is False
    assert snapshot.render() == ""


def test_context_snapshot_truncates_visibly(tmp_path: Path) -> None:
    store = BuiltinMemoryStore(
        root=tmp_path,
        policy=MemoryPolicy(),
        max_file_chars=10_000,
        inject_file_chars=40,
        inject_total_chars=80,
    )
    (tmp_path / "MEMORY.md").parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "MEMORY.md").write_text("# Memory\n\n" + "x" * 200, encoding="utf-8")

    snapshot = store.context_snapshot()

    assert snapshot.targets["memory"].truncated is True
    assert "Memory file truncated: MEMORY.md exceeded" in snapshot.render()


def test_add_rejects_file_growth_over_hard_limit(tmp_path: Path) -> None:
    store = BuiltinMemoryStore(root=tmp_path, policy=MemoryPolicy(), max_file_chars=80)

    result = store.add(
        MemoryAddRequest(
            target=MemoryTarget.USER,
            content="x" * 200,
            source="explicit_user_request",
        )
    )

    assert result.status == "rejected_by_policy"
    assert result.reason == "memory_file_too_large"
