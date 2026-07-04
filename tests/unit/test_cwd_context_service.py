from pathlib import Path
from uuid import uuid4

import pytest

from awesome_agent.runtime.cwd_context import (
    CwdContextService,
    InMemoryCwdContextSnapshotRepository,
)


@pytest.mark.asyncio
async def test_reads_direct_agents_and_claude_with_agents_precedence(
    tmp_path: Path,
) -> None:
    (tmp_path / "AGENTS.md").write_text(
        "Use concise answers.\nKeep tests focused.\n",
        encoding="utf-8",
    )
    (tmp_path / "CLAUDE.md").write_text(
        "Use concise answers.\nMention assumptions.\n",
        encoding="utf-8",
    )
    service = CwdContextService(repository=InMemoryCwdContextSnapshotRepository())

    result = await service.evaluate(
        thread_id=uuid4(),
        run_id=uuid4(),
        working_directory=tmp_path,
    )

    assert result.status == "created"
    assert result.snapshot is not None
    assert result.snapshot.precedence == "AGENTS.md > CLAUDE.md"
    assert result.snapshot.files[0].filename == "AGENTS.md"
    assert result.snapshot.files[0].included is True
    assert result.snapshot.files[1].filename == "CLAUDE.md"
    assert result.snapshot.files[1].deduped_lines == 1
    assert "Use concise answers." in result.rendered
    assert result.rendered.count("Use concise answers.") == 1
    assert "Mention assumptions." in result.rendered
    assert "CLAUDE.md cannot override AGENTS.md" in result.rendered


@pytest.mark.asyncio
async def test_only_reads_working_directory_direct_children(tmp_path: Path) -> None:
    child = tmp_path / "src"
    child.mkdir()
    (tmp_path / "AGENTS.md").write_text("parent rule", encoding="utf-8")
    service = CwdContextService(repository=InMemoryCwdContextSnapshotRepository())

    result = await service.evaluate(
        thread_id=uuid4(),
        run_id=uuid4(),
        working_directory=child,
    )

    assert result.status == "none_found"
    assert result.rendered == ""
    assert all(item["exists"] is False for item in result.evidence["files"])


@pytest.mark.asyncio
async def test_oversized_file_is_skipped_without_truncation(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_bytes(b"x" * (128 * 1024 + 1))
    service = CwdContextService(repository=InMemoryCwdContextSnapshotRepository())

    result = await service.evaluate(
        thread_id=uuid4(),
        run_id=uuid4(),
        working_directory=tmp_path,
    )

    assert result.status == "created"
    assert result.snapshot is not None
    file = result.snapshot.files[0]
    assert file.filename == "AGENTS.md"
    assert file.included is False
    assert file.skipped_reason == "oversize"
    assert "AGENTS.md was skipped because it exceeded 131072 bytes." in result.rendered
    assert "x" * 200 not in result.rendered


@pytest.mark.asyncio
async def test_missing_and_invalid_directory_are_soft_failures(
    tmp_path: Path,
) -> None:
    service = CwdContextService(repository=InMemoryCwdContextSnapshotRepository())

    missing = await service.evaluate(
        thread_id=uuid4(),
        run_id=uuid4(),
        working_directory=tmp_path,
    )
    invalid = await service.evaluate(
        thread_id=uuid4(),
        run_id=uuid4(),
        working_directory=tmp_path / "missing",
    )

    assert missing.status == "none_found"
    assert missing.rendered == ""
    assert invalid.status == "disabled_invalid_working_directory"
    assert invalid.rendered == ""


@pytest.mark.asyncio
async def test_reuses_snapshot_id_when_file_fingerprint_is_unchanged(
    tmp_path: Path,
) -> None:
    (tmp_path / "AGENTS.md").write_text("Stable rule.\n", encoding="utf-8")
    repository = InMemoryCwdContextSnapshotRepository()
    service = CwdContextService(repository=repository)
    thread_id = uuid4()

    first = await service.evaluate(
        thread_id=thread_id,
        run_id=uuid4(),
        working_directory=tmp_path,
    )
    second = await service.evaluate(
        thread_id=thread_id,
        run_id=uuid4(),
        working_directory=tmp_path,
    )

    assert first.status == "created"
    assert second.status == "reused"
    assert first.snapshot is not None
    assert second.snapshot is not None
    assert second.snapshot.id == first.snapshot.id
    assert second.rendered == first.rendered
