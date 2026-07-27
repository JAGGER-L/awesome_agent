from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from awesome_agent.config.model_transaction import (
    ProviderModelTransactionJournal,
    ProviderModelTransactionJournalError,
    ProviderModelTransactionPhase,
    ProviderModelTransactionRecord,
)


def _record() -> ProviderModelTransactionRecord:
    return ProviderModelTransactionRecord(
        transaction_id="0" * 32,
        phase=ProviderModelTransactionPhase.PREPARED,
        thread_id="thread_123",
        previous_default_model="deepseek/deepseek-v4-flash",
        target_default_model="deepseek/deepseek-v4-pro",
        previous_thread_model="kimi/kimi-k2.5",
        target_thread_model="deepseek/deepseek-v4-pro",
    )


def test_model_transaction_journal_round_trip_is_atomic_and_secret_free(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state" / "provider-model-transaction.json"
    journal = ProviderModelTransactionJournal(path)
    prepared = journal.prepare(_record())

    assert journal.read() == prepared
    raw = path.read_text(encoding="utf-8")
    assert "secret" not in raw.lower()
    assert "api_key" not in raw.lower()

    committed = journal.mark_committed(prepared)
    assert committed.phase is ProviderModelTransactionPhase.COMMITTED
    assert journal.read() == committed

    journal.clear(committed)
    assert journal.read() is None


def test_model_transaction_identity_prevents_equal_endpoint_aba() -> None:
    first = _record()
    second = first.model_copy(update={"transaction_id": "1" * 32})

    assert first != second
    assert first.transaction_id == "0" * 32
    assert second.transaction_id == "1" * 32


def test_model_transaction_identity_reads_legacy_version_one_records() -> None:
    record = ProviderModelTransactionRecord.model_validate(
        {
            "version": 1,
            "phase": "prepared",
            "thread_id": "thread_legacy",
            "previous_default_model": None,
            "target_default_model": "deepseek/deepseek-v4-pro",
            "previous_thread_model": None,
            "target_thread_model": "deepseek/deepseek-v4-pro",
        }
    )

    assert record.transaction_id == "legacy"


@pytest.mark.parametrize(
    "raw",
    (
        b"\xff",
        b'{"version":1,"version":1}',
        b'{"version":1,"phase":NaN}',
        b"{" + (b" " * 4096) + b"}",
        b'{"version":1,"phase":"prepared","unknown":true}',
    ),
)
def test_model_transaction_journal_rejects_invalid_or_oversized_input(
    tmp_path: Path,
    raw: bytes,
) -> None:
    path = tmp_path / "provider-model-transaction.json"
    path.write_bytes(raw)

    with pytest.raises(ProviderModelTransactionJournalError):
        ProviderModelTransactionJournal(path).read()


def test_model_transaction_journal_rejects_file_links_without_opening_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = tmp_path / "state"
    state.mkdir()
    sentinel = tmp_path / "external-sentinel"
    sentinel_content = "outside-journal-sentinel"
    sentinel.write_text(sentinel_content, encoding="utf-8")

    hardlink = state / "provider-model-transaction.json"
    os.link(sentinel, hardlink)
    open_spy = Mock(side_effect=AssertionError("external sentinel was opened"))
    monkeypatch.setattr("awesome_agent.config.model_transaction.os.open", open_spy)
    with pytest.raises(ProviderModelTransactionJournalError) as captured:
        ProviderModelTransactionJournal(hardlink).read()
    open_spy.assert_not_called()
    assert sentinel_content not in str(captured.value)
    assert sentinel.read_text(encoding="utf-8") == sentinel_content

    if os.name != "nt":
        hardlink.unlink()
        symlink = state / "provider-model-transaction.json"
        symlink.symlink_to(sentinel)
        open_spy.reset_mock()
        with pytest.raises(ProviderModelTransactionJournalError) as captured:
            ProviderModelTransactionJournal(symlink).read()
        open_spy.assert_not_called()
        assert sentinel_content not in str(captured.value)
        assert sentinel.read_text(encoding="utf-8") == sentinel_content


def test_model_transaction_journal_rejects_linked_state_directory_without_reading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    external = tmp_path / "external"
    external.mkdir()
    sentinel_content = "outside-state-sentinel"
    (external / "provider-model-transaction.json").write_text(
        sentinel_content,
        encoding="utf-8",
    )
    linked_state = tmp_path / "state"
    if os.name == "nt":
        created = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(linked_state), str(external)],
            check=False,
            capture_output=True,
            text=True,
        )
        assert created.returncode == 0, created.stderr
    else:
        linked_state.symlink_to(external, target_is_directory=True)

    open_spy = Mock(side_effect=AssertionError("external sentinel was opened"))
    monkeypatch.setattr("awesome_agent.config.model_transaction.os.open", open_spy)
    try:
        with pytest.raises(ProviderModelTransactionJournalError) as captured:
            ProviderModelTransactionJournal(
                linked_state / "provider-model-transaction.json"
            ).read()
        open_spy.assert_not_called()
        assert sentinel_content not in str(captured.value)
        assert (external / "provider-model-transaction.json").read_text(
            encoding="utf-8"
        ) == sentinel_content
    finally:
        if os.name == "nt":
            linked_state.rmdir()
        else:
            linked_state.unlink()


def test_model_transaction_record_rejects_unknown_models_and_mismatched_target() -> (
    None
):
    with pytest.raises(ValidationError):
        ProviderModelTransactionRecord(
            phase=ProviderModelTransactionPhase.PREPARED,
            thread_id="thread_123",
            previous_default_model=None,
            target_default_model="unknown/model",
            previous_thread_model=None,
            target_thread_model="unknown/model",
        )
    with pytest.raises(ValidationError):
        ProviderModelTransactionRecord(
            phase=ProviderModelTransactionPhase.PREPARED,
            thread_id="thread_123",
            previous_default_model=None,
            target_default_model="deepseek/deepseek-v4-pro",
            previous_thread_model=None,
            target_thread_model="kimi/kimi-k2.6",
        )
