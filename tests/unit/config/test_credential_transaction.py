from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from awesome_agent.config import (
    CredentialSource,
    ProviderCredentialTransactionError,
    ProviderCredentialTransactionJournal,
    ProviderCredentialTransactionPhase,
    ProviderCredentialTransactionRecord,
    SecretFileSnapshot,
)


def _journal(tmp_path: Path) -> ProviderCredentialTransactionJournal:
    return ProviderCredentialTransactionJournal(
        tmp_path / ".provider-credential-transaction.json",
        tmp_path / ".provider-credential-transaction.env",
    )


def _record(
    previous: SecretFileSnapshot,
    target: SecretFileSnapshot,
) -> ProviderCredentialTransactionRecord:
    return ProviderCredentialTransactionRecord(
        phase=ProviderCredentialTransactionPhase.PREPARED,
        service="deepseek",
        environment_variable="DEEPSEEK_API_KEY",
        action="replace",
        previous_source=CredentialSource.ENVIRONMENT,
        target_source=CredentialSource.AWESOME,
        previous_env_existed=previous.existed,
        previous_env_sha256=previous.content_hash,
        target_env_existed=target.existed,
        target_env_sha256=target.content_hash,
    )


def test_credential_journal_contains_no_secret_and_backup_preserves_full_env(
    tmp_path: Path,
) -> None:
    previous = SecretFileSnapshot(
        existed=True,
        content=(
            b"# keep this comment\n"
            b"DEEPSEEK_API_KEY=old-secret\n"
            b"MOONSHOT_API_KEY=unrelated-secret\n"
        ),
    )
    target = SecretFileSnapshot(
        existed=True,
        content=(
            b"# keep this comment\n"
            b"DEEPSEEK_API_KEY=new-secret\n"
            b"MOONSHOT_API_KEY=unrelated-secret\n"
        ),
    )
    journal = _journal(tmp_path)

    journal.stage_backup(previous)
    prepared = journal.prepare(_record(previous, target))

    journal_raw = journal.path.read_bytes()
    assert b"old-secret" not in journal_raw
    assert b"new-secret" not in journal_raw
    assert b"unrelated-secret" not in journal_raw
    assert journal.read_backup(prepared) == previous
    assert journal.backup_path.read_bytes() == previous.content
    if os.name != "nt":
        assert stat.S_IMODE(journal.path.stat().st_mode) == 0o600
        assert stat.S_IMODE(journal.backup_path.stat().st_mode) == 0o600


def test_credential_journal_advances_atomically_and_cleans_both_files(
    tmp_path: Path,
) -> None:
    previous = SecretFileSnapshot(existed=False, content=b"")
    target = SecretFileSnapshot(
        existed=True,
        content=b"DEEPSEEK_API_KEY=new-secret\n",
    )
    journal = _journal(tmp_path)
    journal.stage_backup(previous)
    prepared = journal.prepare(_record(previous, target))

    secret_committed = journal.mark_secret_committed(prepared)
    committed = journal.mark_committed(secret_committed)

    assert journal.read() == committed
    assert committed.phase is ProviderCredentialTransactionPhase.COMMITTED
    journal.clear(committed)
    journal.require_clean()
    assert not journal.path.exists()
    assert not journal.backup_path.exists()


def test_orphan_backup_is_cleanable_because_mutation_has_not_started(
    tmp_path: Path,
) -> None:
    journal = _journal(tmp_path)
    previous = SecretFileSnapshot(
        existed=True,
        content=b"DEEPSEEK_API_KEY=old-secret\n",
    )

    journal.stage_backup(previous)

    assert journal.read() is None
    assert journal.clear_orphan_backup() is True
    assert journal.clear_orphan_backup() is False


def test_credential_backup_rejects_hardlink_without_touching_external_file(
    tmp_path: Path,
) -> None:
    journal = _journal(tmp_path)
    external = tmp_path / "external-sentinel"
    external.write_bytes(b"outside-secret")
    os.link(external, journal.backup_path)
    previous = SecretFileSnapshot(existed=True, content=b"old-secret")

    with pytest.raises(ProviderCredentialTransactionError):
        journal.stage_backup(previous)

    assert external.read_bytes() == b"outside-secret"
    assert journal.backup_path.stat().st_nlink == 2
    assert not journal.path.exists()


def test_credential_journal_rejects_secret_fields_and_invalid_contract(
    tmp_path: Path,
) -> None:
    journal = _journal(tmp_path)
    journal.path.write_text(
        json.dumps(
            {
                "version": 1,
                "phase": "prepared",
                "service": "deepseek",
                "environment_variable": "DEEPSEEK_API_KEY",
                "action": "replace",
                "previous_source": None,
                "target_source": "awesome",
                "previous_env_existed": False,
                "previous_env_sha256": "0" * 64,
                "target_env_existed": True,
                "target_env_sha256": "1" * 64,
                "api_key": "must-never-be-accepted",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ProviderCredentialTransactionError):
        journal.read()
