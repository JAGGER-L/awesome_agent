from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from zipfile import ZipFile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.release.verify_bundle import (
    BundleVerificationError,
    create_historical_schema_five,
    find_payload,
    resolve_executable,
    verify_upgraded_database,
)

from awesome_agent.storage.database import (
    _MIGRATIONS,
    initialize_application_database,
)


def test_find_payload_rejects_wrong_prefix(tmp_path: Path) -> None:
    bundle = tmp_path / "awesome-1.1.1.zip"
    with ZipFile(bundle, "w") as archive:
        archive.writestr("wrong-prefix/VERSION", "1.1.1\n")

    with (
        ZipFile(bundle) as archive,
        pytest.raises(BundleVerificationError, match="bundle prefix"),
    ):
        find_payload(archive, "1.1.1")


def test_resolve_executable_accepts_windows_command_shim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "scripts.release.verify_bundle.shutil.which",
        lambda name: "C:/node/npm.cmd" if name == "npm.cmd" else None,
    )

    assert resolve_executable("npm") == "C:/node/npm.cmd"


def test_create_historical_schema_five_preserves_seeded_state(
    tmp_path: Path,
) -> None:
    database = tmp_path / "application.db"
    create_historical_schema_five(
        database,
        {version: _MIGRATIONS[version] for version in range(1, 6)},
    )

    with sqlite3.connect(database) as connection:
        trust = connection.execute(
            "SELECT workspace_key FROM trusted_workspaces"
        ).fetchone()
        thread = connection.execute("SELECT thread_id FROM threads").fetchone()
        entry = connection.execute(
            "SELECT entry_id, kind, content FROM thread_entries"
        ).fetchone()
        version = connection.execute("PRAGMA user_version").fetchone()

    assert trust == ("workspace_release_smoke",)
    assert thread == ("thread_release_smoke",)
    assert entry == (
        "entry_release_smoke",
        "user_message",
        "historical message",
    )
    assert version == (5,)


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        ("version", "schema version"),
        ("identity", "client identity"),
    ],
)
def test_verify_upgraded_database_requires_version_six_and_client_identity(
    tmp_path: Path,
    mutation: str,
    match: str,
) -> None:
    database = tmp_path / "application.db"
    create_historical_schema_five(
        database,
        {version: _MIGRATIONS[version] for version in range(1, 6)},
    )
    initialize_application_database(database)

    with sqlite3.connect(database) as connection:
        if mutation == "version":
            connection.execute("PRAGMA user_version = 5")
        else:
            connection.execute("DROP TRIGGER trg_thread_entries_client_identity_update")
            connection.execute("UPDATE thread_entries SET client_message_id = 'wrong'")
        connection.commit()

    with pytest.raises(BundleVerificationError, match=match):
        verify_upgraded_database(database, expected_schema_version=6)


def test_verify_upgraded_database_accepts_migrated_state(tmp_path: Path) -> None:
    database = tmp_path / "application.db"
    create_historical_schema_five(
        database,
        {version: _MIGRATIONS[version] for version in range(1, 6)},
    )
    initialize_application_database(database)

    verify_upgraded_database(database, expected_schema_version=6)
