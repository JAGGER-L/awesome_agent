import sqlite3
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock

import pytest

from awesome_agent.application import composition
from awesome_agent.application.contracts import (
    ApplicationResult,
    InitializeStatus,
    ProductErrorCode,
    ThreadListQuery,
)
from awesome_agent.config import load_config_sources
from awesome_agent.core.events import CollectingEventSink, InteractionRequiredPayload
from awesome_agent.core.workspace import (
    TrustStatus,
    WorkspaceTrustService,
    resolve_workspace,
)
from awesome_agent.extensions.skills import discover_skills
from awesome_agent.storage.database import initialize_application_database
from awesome_agent.storage.state_lease import StateLease, StateLeaseMode
from awesome_agent.storage.trust import SQLiteWorkspaceTrustStore


def _unwrap[T](result: ApplicationResult[T]) -> T:
    assert result.ok is True
    assert result.value is not None
    return result.value


def _file_snapshot(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_accept_survives_reopen_and_revoke(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    database = tmp_path / "home" / "state" / "application.db"
    identity = resolve_workspace(workspace)

    first = WorkspaceTrustService(SQLiteWorkspaceTrustStore(database))
    assert first.status(identity) is TrustStatus.UNKNOWN
    first.accept(identity)

    reopened = WorkspaceTrustService(SQLiteWorkspaceTrustStore(database))
    assert reopened.status(identity) is TrustStatus.TRUSTED
    assert reopened.revoke(identity) is True
    assert reopened.status(identity) is TrustStatus.UNKNOWN


def test_decline_is_represented_by_not_calling_accept(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    database = tmp_path / "application.db"
    identity = resolve_workspace(workspace)

    service = WorkspaceTrustService(SQLiteWorkspaceTrustStore(database))
    assert service.status(identity) is TrustStatus.UNKNOWN

    reopened = WorkspaceTrustService(SQLiteWorkspaceTrustStore(database))
    assert reopened.status(identity) is TrustStatus.UNKNOWN


def test_moved_workspace_requires_new_trust(tmp_path: Path) -> None:
    original = tmp_path / "original"
    moved = tmp_path / "moved"
    original.mkdir()
    database = tmp_path / "application.db"
    service = WorkspaceTrustService(SQLiteWorkspaceTrustStore(database))
    service.accept(resolve_workspace(original))

    original.rename(moved)

    assert service.status(resolve_workspace(moved)) is TrustStatus.UNKNOWN


def test_symlink_and_real_path_share_identity_and_trust(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    link = tmp_path / "workspace-link"
    try:
        link.symlink_to(workspace, target_is_directory=True)
    except OSError:
        pytest.skip("Directory symlinks are not available on this platform.")

    real_identity = resolve_workspace(workspace)
    link_identity = resolve_workspace(link)
    service = WorkspaceTrustService(
        SQLiteWorkspaceTrustStore(tmp_path / "application.db")
    )
    service.accept(real_identity)

    assert link_identity.key == real_identity.key
    assert service.status(link_identity) is TrustStatus.TRUSTED


@pytest.mark.asyncio
async def test_branch_is_not_read_before_trust_and_is_cached_afterward(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    calls: list[Path] = []
    config_loader = Mock(wraps=load_config_sources)
    skill_discovery = Mock(wraps=discover_skills)
    mcp_config_builder = Mock(wraps=composition._mcp_configs)

    def branch_reader(path: Path) -> str:
        calls.append(path)
        return "feature/auth"

    monkeypatch.setattr(composition, "_git_branch", branch_reader)
    monkeypatch.setattr(composition, "load_config_sources", config_loader)
    monkeypatch.setattr(composition, "discover_skills", skill_discovery)
    monkeypatch.setattr(composition, "_mcp_configs", mcp_config_builder)
    application = await composition.compose_local_application(
        home=tmp_path / "home",
        workspace=workspace,
        event_sink=CollectingEventSink(),
        environ={},
    )

    pending = _unwrap(await application.initialize())
    assert pending.status is InitializeStatus.TRUST_REQUIRED
    assert pending.workspace.display_path == str(workspace)
    assert pending.workspace.branch is None
    assert calls == []
    assert [
        call.kwargs["workspace_trusted"] for call in config_loader.call_args_list
    ] == [False]
    assert skill_discovery.call_count == 0
    assert mcp_config_builder.call_count == 0

    assert pending.interaction_id is not None
    _unwrap(await application.respond_interaction(pending.interaction_id, "trust"))
    ready = _unwrap(await application.initialize())
    state = _unwrap(await application.get_state())

    assert ready.workspace.branch == "feature/auth"
    assert state.workspace.branch == "feature/auth"
    assert state.current_thread_id is None
    assert _unwrap(await application.list_threads(ThreadListQuery())).threads == ()
    assert calls == [workspace.resolve()]
    assert [
        call.kwargs["workspace_trusted"] for call in config_loader.call_args_list
    ] == [False, True]
    assert skill_discovery.call_count == 1
    assert mcp_config_builder.call_count == 1
    _unwrap(await application.shutdown())


@pytest.mark.asyncio
async def test_older_state_requests_reset_without_mutation(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    state = home / "state"
    state.mkdir(parents=True)
    database = state / "application.db"
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA user_version = 1")
    config = home / "config.yaml"
    config.write_text("providers: {}\n", encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    before_state = _file_snapshot(state)
    before_config = config.read_bytes()

    sink = CollectingEventSink()
    application = await composition.compose_local_application(
        home=home,
        workspace=workspace,
        event_sink=sink,
        environ={},
    )
    result = await application.initialize()

    pending = _unwrap(result)
    assert pending.status is InitializeStatus.STATE_RESET_REQUIRED
    assert pending.interaction_id is not None
    payload = next(
        event.payload
        for event in sink.events
        if isinstance(event.payload, InteractionRequiredPayload)
    )
    assert isinstance(payload, InteractionRequiredPayload)
    assert payload.interaction_kind == "state_reset"
    assert [choice.label for choice in payload.choices] == [
        "Reset local state and continue",
        "Exit",
    ]
    assert _file_snapshot(state) == before_state
    assert config.read_bytes() == before_config

    denied = _unwrap(
        await application.respond_interaction(pending.interaction_id, "deny")
    )
    assert denied.accepted is True
    assert denied.status == "denied"
    assert _file_snapshot(state) == before_state
    assert config.read_bytes() == before_config
    _unwrap(await application.shutdown())


@pytest.mark.asyncio
async def test_confirmed_reset_preserves_nonstate_data_and_continues_to_trust(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    state = home / "state"
    state.mkdir(parents=True)
    database = state / "application.db"
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("CREATE TABLE legacy_state (value TEXT NOT NULL)")
        connection.execute("INSERT INTO legacy_state VALUES ('old')")
        connection.execute("PRAGMA user_version = 6")
        connection.commit()
    (state / "checkpoints.db").write_bytes(b"checkpoint")
    (state / "change-journal").mkdir()
    (state / "change-journal" / "blob").write_bytes(b"change")
    config = home / "config.yaml"
    config.write_bytes(b"providers: {}\n")
    memory = home / "memory" / "MEMORY.md"
    memory.parent.mkdir()
    memory.write_bytes(b"remember")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    application = await composition.compose_local_application(
        home=home,
        workspace=workspace,
        event_sink=CollectingEventSink(),
        environ={},
    )

    pending = _unwrap(await application.initialize())
    assert pending.status is InitializeStatus.STATE_RESET_REQUIRED
    assert pending.interaction_id is not None
    reset = _unwrap(
        await application.respond_interaction(
            pending.interaction_id,
            "reset_state",
        )
    )

    assert reset.accepted is True
    assert reset.status == "resolved"
    trust = _unwrap(await application.initialize())
    assert trust.status is InitializeStatus.TRUST_REQUIRED
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 7
    assert not (state / "checkpoints.db").exists()
    assert not (state / "change-journal").exists()
    assert config.read_bytes() == b"providers: {}\n"
    assert memory.read_bytes() == b"remember"
    _unwrap(await application.shutdown())


@pytest.mark.asyncio
async def test_busy_reset_keeps_same_interaction_available_for_retry(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    database = home / "state" / "application.db"
    database.parent.mkdir(parents=True)
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("PRAGMA user_version = 6")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    application = await composition.compose_local_application(
        home=home,
        workspace=workspace,
        event_sink=CollectingEventSink(),
        environ={},
    )
    pending = _unwrap(await application.initialize())
    assert pending.interaction_id is not None
    blocker = StateLease.acquire(home, StateLeaseMode.SHARED)
    try:
        failed = _unwrap(
            await application.respond_interaction(
                pending.interaction_id,
                "reset_state",
            )
        )
    finally:
        blocker.close()

    assert failed.accepted is False
    assert failed.status == "state_reset_busy"
    assert failed.error is not None
    assert failed.error.code is ProductErrorCode.STATE_RESET_BUSY
    repeated = _unwrap(await application.initialize())
    assert repeated.interaction_id == pending.interaction_id

    recovered = _unwrap(
        await application.respond_interaction(
            pending.interaction_id,
            "reset_state",
        )
    )
    assert recovered.accepted is True
    _unwrap(await application.shutdown())


@pytest.mark.asyncio
async def test_reset_confirmation_preserves_state_that_became_current(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    database = home / "state" / "application.db"
    database.parent.mkdir(parents=True)
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("PRAGMA user_version = 6")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    application = await composition.compose_local_application(
        home=home,
        workspace=workspace,
        event_sink=CollectingEventSink(),
        environ={},
    )
    pending = _unwrap(await application.initialize())
    assert pending.interaction_id is not None
    database.unlink()
    initialize_application_database(database)
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("CREATE TABLE preserved_marker (value TEXT)")
        connection.commit()

    response = _unwrap(
        await application.respond_interaction(
            pending.interaction_id,
            "reset_state",
        )
    )

    assert response.accepted is True
    with closing(sqlite3.connect(database)) as connection:
        marker = connection.execute(
            "SELECT name FROM sqlite_schema WHERE name = 'preserved_marker'"
        ).fetchone()
    assert marker is not None
    _unwrap(await application.shutdown())


@pytest.mark.asyncio
async def test_reset_confirmation_rejects_state_that_became_newer(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    database = home / "state" / "application.db"
    database.parent.mkdir(parents=True)
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("PRAGMA user_version = 6")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    application = await composition.compose_local_application(
        home=home,
        workspace=workspace,
        event_sink=CollectingEventSink(),
        environ={},
    )
    pending = _unwrap(await application.initialize())
    assert pending.interaction_id is not None
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("PRAGMA user_version = 8")
    before = database.read_bytes()

    response = _unwrap(
        await application.respond_interaction(
            pending.interaction_id,
            "reset_state",
        )
    )

    assert response.accepted is False
    assert response.error is not None
    assert response.error.code is ProductErrorCode.STATE_CREATED_BY_NEWER_VERSION
    assert database.read_bytes() == before
    repeated = await application.initialize()
    assert repeated.ok is False
    assert repeated.error is not None
    assert repeated.error.code is ProductErrorCode.STATE_CREATED_BY_NEWER_VERSION
    _unwrap(await application.shutdown())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("version", "code"),
    [
        (8, ProductErrorCode.STATE_CREATED_BY_NEWER_VERSION),
        (999, ProductErrorCode.STATE_CREATED_BY_NEWER_VERSION),
    ],
)
async def test_newer_state_is_not_offered_destructive_recovery(
    tmp_path: Path,
    version: int,
    code: ProductErrorCode,
) -> None:
    home = tmp_path / "home"
    database = home / "state" / "application.db"
    database.parent.mkdir(parents=True)
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(f"PRAGMA user_version = {version}")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    before = database.read_bytes()
    application = await composition.compose_local_application(
        home=home,
        workspace=workspace,
        event_sink=CollectingEventSink(),
        environ={},
    )

    result = await application.initialize()

    assert result.ok is False
    assert result.error is not None
    assert result.error.code is code
    assert result.error.data["found_schema"] == version
    assert database.read_bytes() == before
    _unwrap(await application.shutdown())


@pytest.mark.asyncio
async def test_unknown_state_is_not_offered_destructive_recovery(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    database = home / "state" / "application.db"
    database.parent.mkdir(parents=True)
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("CREATE TABLE unknown_state (value TEXT)")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    before = database.read_bytes()
    application = await composition.compose_local_application(
        home=home,
        workspace=workspace,
        event_sink=CollectingEventSink(),
        environ={},
    )

    result = await application.initialize()

    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ProductErrorCode.STATE_UNKNOWN
    assert database.read_bytes() == before
    _unwrap(await application.shutdown())


@pytest.mark.asyncio
async def test_corrupt_state_is_unavailable_without_reset_interaction(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    database = home / "state" / "application.db"
    database.parent.mkdir(parents=True)
    database.write_bytes(b"not a sqlite database")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    before = database.read_bytes()
    sink = CollectingEventSink()
    application = await composition.compose_local_application(
        home=home,
        workspace=workspace,
        event_sink=sink,
        environ={},
    )

    result = await application.initialize()

    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ProductErrorCode.STATE_UNAVAILABLE
    assert result.error.retryable is True
    assert database.read_bytes() == before
    assert not any(
        isinstance(event.payload, InteractionRequiredPayload) for event in sink.events
    )
    _unwrap(await application.shutdown())
