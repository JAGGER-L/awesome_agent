from __future__ import annotations

import asyncio
import gc
import json
import os
import sqlite3
import threading
import weakref
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager, suppress
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from typing import Any, ClassVar, cast

import pytest
from pydantic import SecretStr

from awesome_agent.application import composition
from awesome_agent.application.command_results import (
    CommandError,
    CommandOutcome,
    CommandResult,
    WebStatusCommandPayload,
)
from awesome_agent.application.commands import CommandIntent, CommandName
from awesome_agent.application.contracts import (
    InitializeStatus,
    InteractionResult,
    ProductErrorCode,
    ProviderCredentialSetRequest,
)
from awesome_agent.application.errors import ApplicationFailure
from awesome_agent.application.facade import LocalApplication
from awesome_agent.application.middleware import ApplicationObservation
from awesome_agent.application.provider_configuration import (
    ProviderConfigurationPublication,
    ProviderConfigurationRecoveryRequired,
    ProviderConfigurationSnapshot,
    reconcile_provider_credential_transaction,
)
from awesome_agent.application.turns import (
    RecoveryResult,
    RecoveryStatus,
    TurnCoordinator,
)
from awesome_agent.application.web_commands import WebRuntimeStatus
from awesome_agent.config import (
    BudgetConfig,
    CredentialSource,
    LoadedConfigSources,
    ProviderCredentialTransactionJournal,
    ProviderCredentialTransactionPhase,
    ProviderCredentialTransactionRecord,
    SecretFileSnapshot,
    TurnConfig,
    UserConfigWriter,
    UserSecretStore,
    load_config_sources,
    resolve_application_config,
)
from awesome_agent.conversation import TurnStatus
from awesome_agent.core.events import CollectingEventSink
from awesome_agent.core.workspace import WorkspaceTrustService, resolve_workspace
from awesome_agent.extensions.mcp import McpServerConfig, McpServerStatus
from awesome_agent.extensions.skills import SkillCatalog, discover_skills
from awesome_agent.modeling import ModelGateway
from awesome_agent.paths import AwesomePaths
from awesome_agent.storage import (
    ApplicationSchemaMismatch,
    ApplicationSQLite,
    ApplicationSQLiteClosed,
    StateCompatibility,
    StateLease,
    StateLeaseMode,
    StateLeaseUnavailable,
    StatePreflight,
)
from awesome_agent.storage.trust import SQLiteWorkspaceTrustStore
from awesome_agent.web import (
    WebFetchRequest,
    WebFetchResponse,
    WebSearchRequest,
    WebSearchResponse,
)


async def _trust_workspaces(home: Path, *workspaces: Path) -> None:
    database = ApplicationSQLite(AwesomePaths.from_home(home).application_db)
    await database.initialize()
    try:
        trust = WorkspaceTrustService(SQLiteWorkspaceTrustStore(database))
        for workspace in workspaces:
            await trust.accept(resolve_workspace(workspace))
    finally:
        await database.aclose()


async def _trusted_application(
    tmp_path: Path,
    *,
    environ: dict[str, str] | None = None,
) -> tuple[LocalApplication, composition._LocalApplicationBackend]:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    await _trust_workspaces(home, workspace)
    application = await composition.compose_local_application(
        home=home,
        workspace=workspace,
        event_sink=CollectingEventSink(),
        environ=environ or {},
    )
    backend = cast(composition._LocalApplicationBackend, application._backend)
    return application, backend


@pytest.mark.asyncio
async def test_composed_application_flushes_strict_session_diagnostics(
    tmp_path: Path,
) -> None:
    application, backend = await _trusted_application(tmp_path)

    initialized = await application.initialize()
    state = await application.get_state()
    stopped = await application.shutdown()

    assert initialized.ok is True
    assert state.ok is True
    assert state.value is not None
    assert stopped.ok is True
    active = backend._paths.logs_dir / "application.jsonl"
    raw = active.read_text(encoding="utf-8")
    records = [json.loads(line) for line in raw.splitlines()]
    assert [record["operation"] for record in records] == [
        "initialize",
        "application.getState",
        "shutdown",
    ]
    assert {record["session_id"] for record in records} == {state.value.session_id}
    assert str(tmp_path) not in raw
    assert all("error_code" not in record for record in records)


@pytest.mark.asyncio
async def test_runtime_replacement_does_not_close_session_diagnostics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writers: list[Any] = []

    class TrackingDiagnosticWriter:
        def __init__(self, _logs_dir: Path) -> None:
            self.observations: list[ApplicationObservation] = []
            self.close_calls = 0
            writers.append(self)

        def try_emit(self, observation: ApplicationObservation) -> None:
            self.observations.append(observation)

        async def aclose(self) -> None:
            self.close_calls += 1

    monkeypatch.setattr(
        composition, "ApplicationDiagnosticWriter", TrackingDiagnosticWriter
    )
    application, backend = await _trusted_application(tmp_path)

    assert (await application.initialize()).ok is True
    writer = writers[0]
    await backend._activate()

    assert writer.close_calls == 0
    assert (await application.shutdown()).ok is True
    assert writer.close_calls == 1
    assert [item.operation.value for item in writer.observations] == [
        "initialize",
        "shutdown",
    ]


@pytest.mark.asyncio
async def test_diagnostic_writer_start_failure_does_not_block_composition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class DiagnosticStartFailure(BaseException):
        pass

    def fail_writer(_logs_dir: Path) -> object:
        raise DiagnosticStartFailure

    monkeypatch.setattr(composition, "ApplicationDiagnosticWriter", fail_writer)
    application, _backend = await _trusted_application(tmp_path)

    assert (await application.initialize()).ok is True
    assert (await application.shutdown()).ok is True


@pytest.mark.asyncio
async def test_composition_failure_closes_the_diagnostic_writer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    close_calls = 0

    class TrackingDiagnosticWriter:
        def __init__(self, _logs_dir: Path) -> None:
            pass

        def try_emit(self, _observation: ApplicationObservation) -> None:
            pass

        async def aclose(self) -> None:
            nonlocal close_calls
            close_calls += 1

    def fail_backend(**_arguments: object) -> object:
        raise RuntimeError("backend construction failed")

    monkeypatch.setattr(
        composition, "ApplicationDiagnosticWriter", TrackingDiagnosticWriter
    )
    monkeypatch.setattr(composition, "_LocalApplicationBackend", fail_backend)
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with pytest.raises(RuntimeError, match="backend construction failed"):
        await composition.compose_local_application(
            home=tmp_path / "home",
            workspace=workspace,
            event_sink=CollectingEventSink(),
            environ={},
        )

    assert close_calls == 1


@pytest.mark.asyncio
async def test_facade_construction_failure_closes_stack_and_diagnostic_writer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resource_close_calls = 0
    diagnostic_close_calls = 0

    class TrackingDiagnosticWriter:
        def __init__(self, _logs_dir: Path) -> None:
            pass

        def try_emit(self, _observation: ApplicationObservation) -> None:
            pass

        async def aclose(self) -> None:
            nonlocal diagnostic_close_calls
            diagnostic_close_calls += 1

    def build_backend(**arguments: object) -> object:
        resources = cast(Any, arguments["resources"])

        async def close_resource() -> None:
            nonlocal resource_close_calls
            resource_close_calls += 1

        resources.push_async_callback(close_resource)
        return object()

    def fail_facade(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("facade construction failed")

    monkeypatch.setattr(
        composition, "ApplicationDiagnosticWriter", TrackingDiagnosticWriter
    )
    monkeypatch.setattr(composition, "_LocalApplicationBackend", build_backend)
    monkeypatch.setattr(composition, "LocalApplication", fail_facade)
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with pytest.raises(RuntimeError, match="facade construction failed"):
        await composition.compose_local_application(
            home=tmp_path / "home",
            workspace=workspace,
            event_sink=CollectingEventSink(),
            environ={},
        )

    assert resource_close_calls == 1
    assert diagnostic_close_calls == 1


@pytest.mark.asyncio
async def test_invalid_provider_model_journal_fails_activation_closed(
    tmp_path: Path,
) -> None:
    application, backend = await _trusted_application(tmp_path)
    journal = backend._paths.provider_model_transaction_file
    journal.write_bytes(b'{"version":1,"phase":"prepared","unknown":true}')

    initialized = await application.initialize()

    assert initialized.ok is False
    assert initialized.error is not None
    assert initialized.error.code is ProductErrorCode.RECOVERY_REQUIRED
    await application.shutdown()


@pytest.mark.asyncio
async def test_invalid_provider_credential_journal_fails_before_activation(
    tmp_path: Path,
) -> None:
    application, backend = await _trusted_application(tmp_path)
    journal = backend._paths.provider_credential_transaction_file
    journal.write_bytes(b'{"version":1,"phase":"prepared","unknown":true}')

    initialized = await application.initialize()

    assert initialized.ok is False
    assert initialized.error is not None
    assert initialized.error.code is ProductErrorCode.RECOVERY_REQUIRED
    assert backend._runtime is None
    await application.shutdown()


@pytest.mark.asyncio
async def test_credential_recovery_precedes_the_first_real_config_load(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    home.mkdir()
    workspace.mkdir()
    resolved_paths = AwesomePaths.from_home(home)
    previous_env = SecretFileSnapshot(
        existed=True,
        content=b"# preserve\nDEEPSEEK_API_KEY=old-secret\nOTHER=value\n",
    )
    resolved_paths.env_file.write_bytes(previous_env.content)
    store = UserSecretStore(resolved_paths.env_file)
    target_env = store.plan_set("DEEPSEEK_API_KEY", SecretStr("new-secret"))
    writer = UserConfigWriter(resolved_paths.config_file)
    writer.update(
        lambda current: current.model_copy(
            update={
                "credentials": current.credentials.model_copy(
                    update={"deepseek": CredentialSource.ENVIRONMENT}
                )
            }
        )
    )
    journal = ProviderCredentialTransactionJournal(
        resolved_paths.provider_credential_transaction_file,
        resolved_paths.provider_credential_backup_file,
    )
    record = ProviderCredentialTransactionRecord(
        phase=ProviderCredentialTransactionPhase.PREPARED,
        service="deepseek",
        environment_variable="DEEPSEEK_API_KEY",
        action="replace",
        previous_source=CredentialSource.ENVIRONMENT,
        target_source=CredentialSource.AWESOME,
        previous_env_existed=previous_env.existed,
        previous_env_sha256=previous_env.content_hash,
        target_env_existed=target_env.existed,
        target_env_sha256=target_env.content_hash,
    )
    journal.stage_backup(previous_env)
    journal.prepare(record)
    store.restore(target_env)
    await _trust_workspaces(home, workspace)
    real_load = load_config_sources
    observed_env_at_load: list[bytes] = []

    def observe_load(
        *,
        paths: AwesomePaths,
        workspace: Path,
        workspace_trusted: bool,
        environ: Mapping[str, str] | None = None,
    ) -> LoadedConfigSources:
        observed_env_at_load.append(resolved_paths.env_file.read_bytes())
        return real_load(
            paths=paths,
            workspace=workspace,
            workspace_trusted=workspace_trusted,
            environ=environ,
        )

    monkeypatch.setattr(
        "awesome_agent.application.composition.load_config_sources",
        observe_load,
    )

    application = await composition.compose_local_application(
        home=home,
        workspace=workspace,
        event_sink=CollectingEventSink(),
        environ={"DEEPSEEK_API_KEY": "environment-secret"},
    )
    assert observed_env_at_load == []

    before_initialize = await application.get_state()
    assert before_initialize.ok is True
    assert before_initialize.value is not None
    assert before_initialize.value.initialized is False
    assert before_initialize.value.workspace_trusted is False
    assert before_initialize.value.secret_status.deepseek_api_key is False
    assert before_initialize.value.provider_credentials.deepseek.configured is False
    assert observed_env_at_load == []

    initialized = await application.initialize()

    assert initialized.ok is True
    assert observed_env_at_load
    assert all(content == previous_env.content for content in observed_env_at_load)
    assert writer.read().credentials.deepseek is CredentialSource.ENVIRONMENT
    journal.require_clean()
    await application.shutdown()


@pytest.mark.asyncio
async def test_failed_credential_recovery_can_be_retried_without_publishing_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    home.mkdir()
    workspace.mkdir()
    await _trust_workspaces(home, workspace)
    attempts = 0

    def fail_once(
        *,
        journal: ProviderCredentialTransactionJournal,
        config_writer: UserConfigWriter,
        secret_store: UserSecretStore,
    ) -> bool:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            failure = RuntimeError("synthetic recovery failure")
            raise ProviderConfigurationRecoveryRequired(
                failure,
                (("startup_reconcile", failure),),
            )
        return reconcile_provider_credential_transaction(
            journal=journal,
            config_writer=config_writer,
            secret_store=secret_store,
        )

    monkeypatch.setattr(
        "awesome_agent.application.composition.reconcile_provider_credential_transaction",
        fail_once,
    )
    application = await composition.compose_local_application(
        home=home,
        workspace=workspace,
        event_sink=CollectingEventSink(),
        environ={"DEEPSEEK_API_KEY": "environment-secret"},
    )

    first = await application.initialize()

    assert first.ok is False
    assert first.error is not None
    assert first.error.code is ProductErrorCode.RECOVERY_REQUIRED
    failed_state = await application.get_state()
    assert failed_state.ok is True
    assert failed_state.value is not None
    assert failed_state.value.initialized is False
    assert failed_state.value.workspace_trusted is False
    assert failed_state.value.secret_status.deepseek_api_key is False

    second = await application.initialize()

    assert second.ok is True
    assert second.value is not None
    assert second.value.status.value == "ready"
    assert attempts == 2
    recovered_state = await application.get_state()
    assert recovered_state.ok is True
    assert recovered_state.value is not None
    assert recovered_state.value.initialized is True
    assert recovered_state.value.workspace_trusted is True
    assert recovered_state.value.secret_status.deepseek_api_key is True
    await application.shutdown()


@pytest.mark.asyncio
async def test_runtime_provider_recovery_fence_blocks_all_mutations_but_not_snapshots(
    tmp_path: Path,
) -> None:
    application, backend = await _trusted_application(
        tmp_path,
        environ={"DEEPSEEK_API_KEY": "test-key"},
    )
    initialized = await application.initialize()
    assert initialized.ok is True
    created = await application.execute_command(
        CommandIntent(name=CommandName.NEW, arguments=())
    )
    assert created.ok is True
    runtime = backend._runtime
    assert runtime is not None
    thread_id = runtime.commands.current_thread_id
    assert thread_id is not None
    journal = backend._paths.provider_model_transaction_file
    journal.write_bytes(b'{"version":1,"phase":"prepared","unknown":true}')

    credential = await application.set_provider_credential(
        ProviderCredentialSetRequest(provider="mem0", action="delete")
    )
    turn = await application.submit_turn(
        thread_id,
        "blocked by recovery fence",
        "client_recovery_fence",
    )
    new_thread = await application.execute_command(
        CommandIntent(name=CommandName.NEW, arguments=())
    )
    direct = await application.execute_direct(thread_id, "echo must-not-run")
    status = await application.execute_command(
        CommandIntent(name=CommandName.STATUS, arguments=())
    )
    state = await application.get_state()
    cancel = await application.cancel_operation("operation_not_running")

    for blocked in (credential, turn, new_thread, direct):
        assert blocked.ok is False
        assert blocked.error is not None
        assert blocked.error.code is ProductErrorCode.RECOVERY_REQUIRED
    assert status.ok is True
    assert state.ok is True
    assert state.value is not None
    assert state.value.active_operation_id is None
    assert cancel.ok is True
    assert cancel.value is not None
    assert cancel.value.cancelled is False
    shutdown = await application.shutdown()
    assert shutdown.ok is True


@pytest.mark.asyncio
async def test_same_workspace_runtime_lease_prevents_live_turn_recovery(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    identity = resolve_workspace(workspace)
    await _trust_workspaces(home, workspace)
    first = await composition.compose_local_application(
        home=home,
        workspace=workspace,
        event_sink=CollectingEventSink(),
        environ={},
    )
    first_ready = await first.initialize()
    assert first_ready.ok is True
    first_backend = cast(composition._LocalApplicationBackend, first._backend)
    thread = await first_backend._conversation.create_thread(identity.key)
    turn = await first_backend._conversation.begin_turn(
        thread.id,
        "still running",
        TurnConfig(
            provider="deepseek",
            model="deepseek/deepseek-v4-flash",
            budgets=BudgetConfig(),
        ),
        client_message_id="client_live_turn",
    )

    second = await composition.compose_local_application(
        home=home,
        workspace=workspace,
        event_sink=CollectingEventSink(),
        environ={},
    )
    blocked = await second.initialize()

    assert blocked.ok is False
    assert blocked.error is not None
    assert blocked.error.code is ProductErrorCode.OPERATION_BUSY
    observed = (await first_backend._conversation.read_thread(thread.id)).turns[0]
    assert (observed.id, observed.status) == (turn.id, TurnStatus.IN_PROGRESS)

    await first.shutdown()
    recovered = await second.initialize()
    assert recovered.ok is True
    second_backend = cast(composition._LocalApplicationBackend, second._backend)
    assert (await second_backend._conversation.read_thread(thread.id)).turns[
        0
    ].status is TurnStatus.FAILED
    await second.shutdown()


@pytest.mark.asyncio
async def test_workspace_runtime_lease_uses_filesystem_identity_across_path_aliases(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    alias = Path(f"\\\\?\\{workspace}") if os.name == "nt" else workspace / "."
    direct_identity = resolve_workspace(workspace)
    alias_identity = resolve_workspace(alias)
    assert alias_identity.root_identity == direct_identity.root_identity
    if os.name == "nt":
        assert alias_identity.key != direct_identity.key
    await _trust_workspaces(home, workspace, alias)
    first = await composition.compose_local_application(
        home=home,
        workspace=workspace,
        event_sink=CollectingEventSink(),
        environ={},
    )
    second = await composition.compose_local_application(
        home=home,
        workspace=alias,
        event_sink=CollectingEventSink(),
        environ={},
    )

    assert (await first.initialize()).ok is True
    blocked = await second.initialize()

    assert blocked.ok is False
    assert blocked.error is not None
    assert blocked.error.code is ProductErrorCode.OPERATION_BUSY
    await first.shutdown()
    assert (await second.initialize()).ok is True
    await second.shutdown()


@pytest.mark.asyncio
async def test_workspace_runtime_path_lease_survives_root_replacement(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    await _trust_workspaces(home, workspace)
    first = await composition.compose_local_application(
        home=home,
        workspace=workspace,
        event_sink=CollectingEventSink(),
        environ={},
    )
    assert (await first.initialize()).ok is True
    original = tmp_path / "workspace-original"
    workspace.rename(original)
    workspace.mkdir()
    second = await composition.compose_local_application(
        home=home,
        workspace=workspace,
        event_sink=CollectingEventSink(),
        environ={},
    )

    blocked = await second.initialize()

    assert blocked.ok is False
    assert blocked.error is not None
    assert blocked.error.code is ProductErrorCode.OPERATION_BUSY
    await first.shutdown()
    assert (await second.initialize()).ok is True
    await second.shutdown()


@pytest.mark.asyncio
async def test_entity_lease_failure_releases_already_acquired_path_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application, _ = await _trusted_application(tmp_path)
    original_acquire = StateLease.acquire

    def fail_entity_lease(
        cls: type[StateLease],
        home: Path,
        mode: StateLeaseMode,
    ) -> StateLease:
        del cls
        if ".workspace-entity-leases" in home.parts:
            raise StateLeaseUnavailable(home, mode)
        return original_acquire(home, mode)

    monkeypatch.setattr(StateLease, "acquire", classmethod(fail_entity_lease))

    blocked = await application.initialize()

    assert blocked.ok is False
    assert blocked.error is not None
    assert blocked.error.code is ProductErrorCode.OPERATION_BUSY
    monkeypatch.setattr(StateLease, "acquire", original_acquire)
    assert (await application.initialize()).ok is True
    await application.shutdown()


@pytest.mark.asyncio
async def test_backend_construction_failure_closes_application_sqlite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[ApplicationSQLite] = []
    close = ApplicationSQLite.aclose

    async def observe_close(database: ApplicationSQLite) -> None:
        await close(database)
        closed.append(database)

    def fail_trust_store(database: ApplicationSQLite) -> object:
        del database
        raise RuntimeError("trust store construction failed")

    monkeypatch.setattr(ApplicationSQLite, "aclose", observe_close)
    monkeypatch.setattr(composition, "SQLiteWorkspaceTrustStore", fail_trust_store)

    with pytest.raises(RuntimeError, match="trust store construction failed"):
        await composition.compose_local_application(
            home=tmp_path / "home",
            workspace=tmp_path,
            event_sink=CollectingEventSink(),
            environ={},
        )

    assert len(closed) == 1
    with pytest.raises(ApplicationSQLiteClosed):
        await closed[0].preflight()


@pytest.mark.asyncio
@pytest.mark.parametrize("current_state", [False, True])
async def test_cancelled_state_initialization_publishes_a_shared_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    current_state: bool,
) -> None:
    if current_state:
        application, backend = await _trusted_application(tmp_path)
    else:
        home = tmp_path / "home"
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        application = await composition.compose_local_application(
            home=home,
            workspace=workspace,
            event_sink=CollectingEventSink(),
            environ={},
        )
        backend = cast(composition._LocalApplicationBackend, application._backend)
    entered = asyncio.Event()
    release = asyncio.Event()
    initialize = backend._database.initialize

    async def blocked_initialize() -> None:
        entered.set()
        await release.wait()
        await initialize()

    monkeypatch.setattr(backend._database, "initialize", blocked_initialize)
    initializing = asyncio.create_task(backend._ensure_state_lease())
    await asyncio.wait_for(entered.wait(), timeout=1)
    initializing.cancel("first cancellation")
    await asyncio.sleep(0)
    initializing.cancel("second cancellation")
    release.set()

    with pytest.raises(asyncio.CancelledError) as cancelled:
        await asyncio.wait_for(initializing, timeout=1)

    assert cancelled.value.args == ("first cancellation",)
    assert backend._state_lease is not None
    assert backend._state_lease.active is True
    assert backend._state_lease.mode is StateLeaseMode.SHARED
    assert await backend._database.quick_check() is True
    await application.shutdown()


@pytest.mark.asyncio
async def test_migration_rechecks_exclusively_then_downgrades_before_initialize(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    application = await composition.compose_local_application(
        home=home,
        workspace=workspace,
        event_sink=CollectingEventSink(),
        environ={},
    )
    backend = cast(composition._LocalApplicationBackend, application._backend)
    required = StatePreflight(
        compatibility=StateCompatibility.MIGRATION_REQUIRED,
        found_schema=7,
        expected_schema=8,
    )
    preflights = iter((required, required))
    calls: list[str] = []
    migration_lease: StateLease | None = None
    downgrade = StateLease.downgrade

    async def preflight() -> StatePreflight:
        calls.append("preflight")
        return next(preflights)

    async def migrate(lease: StateLease) -> Path:
        nonlocal migration_lease
        migration_lease = lease
        calls.append(f"migrate:{lease.mode.value}")
        assert lease.active is True
        assert lease.mode is StateLeaseMode.EXCLUSIVE
        return backend._paths.application_db.with_name(
            "application.db.pre-migration.bak"
        )

    def observe_downgrade(lease: StateLease) -> None:
        calls.append(f"downgrade:{lease.mode.value}")
        downgrade(lease)

    async def initialize() -> None:
        assert migration_lease is not None
        calls.append(f"initialize:{migration_lease.mode.value}")
        assert migration_lease.mode is StateLeaseMode.SHARED

    monkeypatch.setattr(backend._database, "preflight", preflight)
    monkeypatch.setattr(backend._database, "migrate", migrate)
    monkeypatch.setattr(backend._database, "initialize", initialize)
    monkeypatch.setattr(StateLease, "downgrade", observe_downgrade)

    await backend._ensure_state_lease()

    assert calls == [
        "preflight",
        "preflight",
        "migrate:exclusive",
        "downgrade:exclusive",
        "initialize:shared",
    ]
    assert backend._state_lease is migration_lease
    assert backend._state_lease is not None
    assert backend._state_lease.mode is StateLeaseMode.SHARED
    await application.shutdown()


@pytest.mark.asyncio
async def test_migration_exclusive_recheck_rejects_changed_state_without_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    application = await composition.compose_local_application(
        home=home,
        workspace=workspace,
        event_sink=CollectingEventSink(),
        environ={},
    )
    backend = cast(composition._LocalApplicationBackend, application._backend)
    preflights = iter(
        (
            StatePreflight(
                compatibility=StateCompatibility.MIGRATION_REQUIRED,
                found_schema=7,
                expected_schema=9,
            ),
            StatePreflight(
                compatibility=StateCompatibility.MIGRATION_UNAVAILABLE,
                found_schema=6,
                expected_schema=9,
            ),
        )
    )

    async def preflight() -> StatePreflight:
        return next(preflights)

    async def reject_migrate(_lease: StateLease) -> None:
        raise AssertionError("migration ran without a confirmed migration path")

    monkeypatch.setattr(backend._database, "preflight", preflight)
    monkeypatch.setattr(backend._database, "migrate", reject_migrate)

    with pytest.raises(ApplicationSchemaMismatch) as raised:
        await backend._ensure_state_lease()

    assert raised.value.direction is StateCompatibility.MIGRATION_UNAVAILABLE
    assert backend._state_lease is None
    await application.shutdown()


@pytest.mark.asyncio
async def test_pending_reset_reclassified_as_migratable_runs_migration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    path = home / "state" / "application.db"
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version = 6")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    application = await composition.compose_local_application(
        home=home,
        workspace=workspace,
        event_sink=CollectingEventSink(),
        environ={},
    )
    backend = cast(composition._LocalApplicationBackend, application._backend)
    initialized = await application.initialize()
    assert initialized.ok is True
    pending = initialized.value
    assert pending is not None
    assert pending.status is InitializeStatus.STATE_RESET_REQUIRED
    assert pending.interaction_id is not None
    calls: list[str] = []
    migration_lease: StateLease | None = None

    async def migratable() -> StatePreflight:
        calls.append("preflight:migration_required")
        return StatePreflight(
            compatibility=StateCompatibility.MIGRATION_REQUIRED,
            found_schema=7,
            expected_schema=8,
        )

    async def migrate(lease: StateLease) -> Path:
        nonlocal migration_lease
        migration_lease = lease
        calls.append(f"migrate:{lease.mode.value}")
        assert lease.mode is StateLeaseMode.EXCLUSIVE
        return path.with_name("application.db.pre-migration.bak")

    async def initialize() -> None:
        assert migration_lease is not None
        calls.append(f"initialize:{migration_lease.mode.value}")
        assert migration_lease.mode is StateLeaseMode.SHARED

    monkeypatch.setattr(backend._database, "preflight", migratable)
    monkeypatch.setattr(backend._database, "migrate", migrate)
    monkeypatch.setattr(backend._database, "initialize", initialize)

    response = await application.respond_interaction(
        pending.interaction_id,
        "reset_state",
    )

    assert response.ok is True
    assert response.value is not None
    assert response.value.accepted is True
    assert calls == [
        "preflight:migration_required",
        "migrate:exclusive",
        "initialize:shared",
    ]
    assert backend._state_lease is migration_lease
    assert backend._state_lease is not None
    assert backend._state_lease.mode is StateLeaseMode.SHARED
    await application.shutdown()


@pytest.mark.asyncio
async def test_state_lease_downgrade_failure_suspends_database_before_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    application = await composition.compose_local_application(
        home=home,
        workspace=workspace,
        event_sink=CollectingEventSink(),
        environ={},
    )
    backend = cast(composition._LocalApplicationBackend, application._backend)
    downgrade = StateLease.downgrade

    def fail_downgrade(lease: StateLease) -> None:
        raise StateLeaseUnavailable(lease.home, StateLeaseMode.SHARED)

    monkeypatch.setattr(StateLease, "downgrade", fail_downgrade)
    with pytest.raises(StateLeaseUnavailable):
        await backend._ensure_state_lease()

    assert backend._state_lease is None
    assert await backend._database.quick_check() is None

    monkeypatch.setattr(StateLease, "downgrade", downgrade)
    await backend._ensure_state_lease()
    assert backend._state_lease is not None
    assert backend._state_lease.mode is StateLeaseMode.SHARED
    assert await backend._database.quick_check() is True
    await application.shutdown()


@pytest.mark.asyncio
async def test_cancel_foreground_responds_while_application_sqlite_is_blocked(
    tmp_path: Path,
) -> None:
    application, backend = await _trusted_application(tmp_path)
    assert (await application.initialize()).ok is True
    database_entered = threading.Event()
    database_release = threading.Event()
    operation_started = asyncio.Event()

    def block_worker(_: object) -> int:
        database_entered.set()
        if not database_release.wait(timeout=2):
            raise AssertionError("Application SQLite worker was not released.")
        return 1

    async def foreground_operation(operation_id: str) -> None:
        del operation_id
        operation_started.set()
        await asyncio.Event().wait()

    blocked_read = asyncio.create_task(backend._database.read(block_worker))
    while not database_entered.is_set():
        await asyncio.sleep(0)
    running = asyncio.create_task(backend._operations.run(foreground_operation))
    await asyncio.wait_for(operation_started.wait(), timeout=1)
    operation_id = backend._operations.active_operation_id
    assert operation_id is not None

    started_at = asyncio.get_running_loop().time()
    cancelled = await asyncio.wait_for(
        application.cancel_operation(operation_id),
        timeout=0.25,
    )
    elapsed = asyncio.get_running_loop().time() - started_at

    assert cancelled.ok is True
    assert cancelled.value is not None
    assert cancelled.value.cancelled is True
    assert elapsed < 0.25
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(running, timeout=1)
    database_release.set()
    assert await asyncio.wait_for(blocked_read, timeout=1) == 1
    await application.shutdown()


@pytest.mark.asyncio
async def test_activation_passes_union_of_disabled_skills_to_discovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.yaml").write_text(
        "skills:\n  disabled: [user_only, shared]\n",
        encoding="utf-8",
    )
    workspace = tmp_path / "workspace"
    (workspace / ".awesome").mkdir(parents=True)
    (workspace / ".awesome" / "config.yaml").write_text(
        "skills:\n  disabled: [workspace_only, shared]\n",
        encoding="utf-8",
    )
    await _trust_workspaces(home, workspace)
    discovered: list[set[str]] = []

    def capture_discovery(
        *,
        bundled_root: Path | None,
        user_root: Path | None,
        workspace_root: Path | None,
        workspace_trusted: bool,
        workspace_anchor: Path | None = None,
        disabled: set[str] | None = None,
    ) -> SkillCatalog:
        discovered.append(set(disabled or ()))
        return discover_skills(
            bundled_root=bundled_root,
            user_root=user_root,
            workspace_root=workspace_root,
            workspace_trusted=workspace_trusted,
            workspace_anchor=workspace_anchor,
            disabled=disabled,
        )

    monkeypatch.setattr(composition, "discover_skills", capture_discovery)
    application = await composition.compose_local_application(
        home=home,
        workspace=workspace,
        event_sink=CollectingEventSink(),
        environ={},
    )

    initialized = await application.initialize()

    assert initialized.ok is True
    assert discovered == [{"user_only", "workspace_only", "shared"}]
    await application.shutdown()


@pytest.mark.asyncio
async def test_selected_model_context_limit_clamps_turn_and_context_budgets(
    tmp_path: Path,
) -> None:
    application, backend = await _trusted_application(
        tmp_path,
        environ={"DEEPSEEK_API_KEY": "test-key"},
    )
    backend._paths.config_file.parent.mkdir(parents=True, exist_ok=True)
    backend._paths.config_file.write_text(
        "providers:\n"
        "  default_model: deepseek/deepseek-v4-flash\n"
        "budgets:\n"
        "  total_context_tokens: 999999\n",
        encoding="utf-8",
    )

    initialized = await application.initialize()

    assert initialized.ok is True
    runtime = backend._runtime
    assert runtime is not None
    context = runtime.context
    assert context._configured_total_tokens == 999_999
    assert context._model_context_limit == 262_144
    thread = await backend._conversation.create_thread(
        backend._workspace.key,
        current_model="deepseek/deepseek-v4-flash",
    )
    assert (
        backend._turn_config(thread, runtime=runtime).budgets.total_context_tokens
        == 262_144
    )
    await application.shutdown()


@pytest.mark.asyncio
async def test_activation_publishes_one_immutable_workspace_runtime(
    tmp_path: Path,
) -> None:
    application, backend = await _trusted_application(tmp_path)

    initialized = await application.initialize()

    assert initialized.ok is True
    runtime = backend._runtime
    assert runtime is not None
    assert runtime.conversation is backend._conversation
    assert runtime.turns is not None
    assert runtime.commands is not None
    assert runtime.command_dispatcher is not None
    assert runtime.tool_registry is not None
    assert runtime.context is not None
    assert runtime.mcp is not None
    assert not hasattr(runtime, "__dict__")
    with pytest.raises(FrozenInstanceError):
        cast(Any, runtime).workspace_branch = "changed"
    await application.shutdown()


@pytest.mark.asyncio
async def test_requests_read_the_published_runtime_without_candidate_fields(
    tmp_path: Path,
) -> None:
    application, backend = await _trusted_application(tmp_path)
    assert (await application.initialize()).ok is True
    runtime = backend._runtime
    assert runtime is not None
    for name in _REMOVED_ACTIVATION_FIELDS:
        assert not hasattr(backend, name)

    created = await application.execute_command(
        CommandIntent(name=CommandName.NEW, arguments=())
    )
    state = await application.get_state()

    assert created.ok is True
    assert state.ok is True
    assert state.value is not None
    assert state.value.current_thread_id == runtime.commands.current_thread_id
    assert state.value.current_thread_id is not None
    await application.shutdown()


@pytest.mark.asyncio
async def test_provider_configuration_replaces_the_runtime_snapshot(
    tmp_path: Path,
) -> None:
    application, backend = await _trusted_application(tmp_path)
    assert (await application.initialize()).ok is True
    created = await application.execute_command(
        CommandIntent(name=CommandName.NEW, arguments=())
    )
    assert created.ok is True
    original = backend._runtime
    assert original is not None
    selected_thread_id = original.commands.current_thread_id
    assert selected_thread_id is not None
    updated_user = original.sources.user.model_copy(
        update={
            "providers": original.sources.user.providers.model_copy(
                update={"default_model": "kimi/kimi-k2.6"}
            )
        }
    )
    updated_sources = replace(original.sources, user=updated_user)
    updated_config = resolve_application_config(updated_sources)

    await backend._apply_provider_configuration(
        (updated_sources, updated_config),
        ProviderConfigurationPublication(1),
    )

    current = backend._runtime
    assert current is not None
    assert current is not original
    assert current.sources is updated_sources
    assert current.application_config is updated_config
    assert current.model_catalog.default_model == "kimi/kimi-k2.6"
    assert original.application_config.providers.default_model is None
    assert current.turns is not original.turns
    assert current.command_dispatcher is not original.command_dispatcher
    assert current.context is not original.context
    assert current.mcp is not original.mcp
    assert current.commands.current_thread_id == selected_thread_id
    assert not hasattr(backend, "_sources")
    assert not hasattr(backend, "_application_config")
    await application.shutdown()


class _TrackingMcpManager:
    instances: ClassVar[list[_TrackingMcpManager]] = []
    hang_first_close: ClassVar[bool] = False

    def __init__(self, **kwargs: object) -> None:
        del kwargs
        self.close_calls = 0
        self.close_cancelled = False
        self._index = len(self.instances)
        self.instances.append(self)

    def configs(self) -> tuple[McpServerConfig, ...]:
        return ()

    def statuses(self) -> tuple[McpServerStatus, ...]:
        return ()

    async def start_enabled(self) -> None:
        return None

    async def aclose(self) -> None:
        self.close_calls += 1
        if self.hang_first_close and self._index == 0:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.close_cancelled = True
                raise


class _BorrowedResource:
    def __init__(self) -> None:
        self.close_calls = 0
        self.exit_calls = 0

    async def aclose(self) -> None:
        self.close_calls += 1

    async def __aexit__(self, *args: object) -> None:
        del args
        self.exit_calls += 1


class _FailingCloseMcpManager(_TrackingMcpManager):
    instances: ClassVar[list[_TrackingMcpManager]] = []

    async def aclose(self) -> None:
        self.close_calls += 1
        raise RuntimeError("resource close failed")


_REMOVED_ACTIVATION_FIELDS = (
    "_initialized",
    "_sources",
    "_application_config",
    "_commands",
    "_command_dispatcher",
    "_diagnostic_commands",
    "_change_commands",
    "_permission_commands",
    "_provider_configuration",
    "_turns",
    "_direct",
    "_extensions",
    "_context",
    "_registry",
    "_local_memory",
    "_mem0_adapter",
    "_mem0_session",
    "_mcp",
    "_change_scope",
    "_change_store",
    "_change_analyzer",
    "_change_operations",
    "_workspace_branch",
    "_workspace_instruction_snapshot",
)


def _assert_activation_rolled_back(
    backend: composition._LocalApplicationBackend,
) -> None:
    assert backend._runtime is None
    assert all(not hasattr(backend, name) for name in _REMOVED_ACTIVATION_FIELDS)


@pytest.mark.asyncio
async def test_activation_failure_closes_candidate_and_retry_does_not_leak(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _TrackingMcpManager.instances = []
    _TrackingMcpManager.hang_first_close = False
    attempts = 0

    async def fail_once(self: TurnCoordinator) -> tuple[object, ...]:
        del self
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("reconcile failed")
        return ()

    monkeypatch.setattr(composition, "McpManager", _TrackingMcpManager)
    monkeypatch.setattr(TurnCoordinator, "reconcile_startup", fail_once)
    application, backend = await _trusted_application(tmp_path)

    with pytest.raises(RuntimeError, match="reconcile failed"):
        await application.initialize()

    first = _TrackingMcpManager.instances[0]
    assert first.close_calls == 1
    _assert_activation_rolled_back(backend)

    initialized = await application.initialize()

    assert initialized.ok is True
    assert len(_TrackingMcpManager.instances) == 2
    assert first.close_calls == 1
    second = _TrackingMcpManager.instances[1]
    assert second.close_calls == 0
    await application.shutdown()
    assert second.close_calls == 1


@pytest.mark.asyncio
async def test_injected_gateway_and_mem0_resources_are_borrowed(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    await _trust_workspaces(home, workspace)
    gateway = _BorrowedResource()
    model_gateway = cast(ModelGateway, gateway)
    mem0 = _BorrowedResource()
    application = await composition.compose_local_application(
        home=home,
        workspace=workspace,
        event_sink=CollectingEventSink(),
        environ={},
        gateway_factory=cast(Any, lambda _provider, _model: model_gateway),
        mem0_client=mem0,
    )
    backend = cast(composition._LocalApplicationBackend, application._backend)

    assert (await application.initialize()).ok is True
    runtime = backend._runtime
    assert runtime is not None
    assert (
        runtime.resources.gateway("deepseek", "deepseek/deepseek-v4-flash")
        is model_gateway
    )
    assert (await application.shutdown()).ok is True

    assert gateway.close_calls == 0
    assert gateway.exit_calls == 0
    assert mem0.close_calls == 0
    assert mem0.exit_calls == 0


@pytest.mark.asyncio
async def test_web_runtime_registers_both_tools_and_retires_managed_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    paths = AwesomePaths.from_home(home)
    UserConfigWriter(paths.config_file).update(
        lambda current: current.model_copy(
            update={"web": current.web.model_copy(update={"enabled": True})}
        )
    )
    await _trust_workspaces(home, workspace)
    closed: list[str] = []

    class FakeWebProvider:
        async def search(self, request: WebSearchRequest) -> WebSearchResponse:
            del request
            return WebSearchResponse(results=())

        async def fetch(self, request: WebFetchRequest) -> WebFetchResponse:
            return WebFetchResponse(url=request.url, content="content")

    @asynccontextmanager
    async def web_resources(*_: object, **__: object) -> AsyncIterator[Any]:
        try:
            yield FakeWebProvider()
        finally:
            closed.append("web")

    monkeypatch.setattr(
        composition,
        "managed_tavily_web_client",
        web_resources,
    )
    application = await composition.compose_local_application(
        home=home,
        workspace=workspace,
        event_sink=CollectingEventSink(),
        environ={"TAVILY_API_KEY": "test-tavily-key"},
    )
    backend = cast(composition._LocalApplicationBackend, application._backend)

    assert (await application.initialize()).ok is True
    first_runtime = backend._runtime
    assert first_runtime is not None
    assert first_runtime.web_available is True
    assert first_runtime.web_diagnostic_code is None
    web_fetch = first_runtime.tool_registry.resolve("web_fetch")
    web_search = first_runtime.tool_registry.resolve("web_search")
    assert web_fetch is not None
    assert web_search is not None
    for missing_tool in ("web_fetch", "web_search"):
        first_runtime.tool_registry.unregister(missing_tool)
        with pytest.raises(
            RuntimeError,
            match="Workspace runtime Web availability is inconsistent",
        ):
            backend._validate_workspace_runtime(first_runtime)
        first_runtime.tool_registry.replace_exact_set(
            ("web_fetch", "web_search"),
            (web_fetch, web_search),
        )
    backend._permission_session.grant_thread_network("thread_test")

    status = await application.execute_command(
        CommandIntent(name=CommandName.WEB, arguments=("status",))
    )
    assert status.ok is True
    assert isinstance(status.value, CommandResult)
    assert isinstance(status.value.payload, WebStatusCommandPayload)
    assert status.value.payload.enabled is True
    assert status.value.payload.available is True
    assert status.value.payload.credential_configured is True

    disabled = await application.execute_command(
        CommandIntent(name=CommandName.WEB, arguments=("off",))
    )
    assert disabled.ok is True
    assert isinstance(disabled.value, CommandResult)
    assert isinstance(disabled.value.payload, WebStatusCommandPayload)
    assert disabled.value.payload.enabled is False
    assert disabled.value.payload.available is False
    assert backend._runtime is not first_runtime
    assert backend._runtime is not None
    assert backend._runtime.tool_registry.resolve("web_fetch") is None
    assert backend._runtime.tool_registry.resolve("web_search") is None
    assert backend._permission_session.thread_granted_capabilities == frozenset()
    for _ in range(20):
        if closed:
            break
        await asyncio.sleep(0)
    assert closed == ["web"]

    assert (await application.shutdown()).ok is True
    assert closed == ["web"]


@pytest.mark.asyncio
async def test_cancelled_web_publication_restores_config_and_current_runtime(
    tmp_path: Path,
) -> None:
    application, backend = await _trusted_application(
        tmp_path,
        environ={"TAVILY_API_KEY": "test-tavily-key"},
    )
    assert (await application.initialize()).ok is True
    original_runtime = backend._runtime
    assert original_runtime is not None
    original_apply = original_runtime.web_commands._apply_configuration
    published = asyncio.Event()
    release = asyncio.Event()

    async def pause_after_publication(
        snapshot: ProviderConfigurationSnapshot,
    ) -> WebRuntimeStatus:
        status = await original_apply(snapshot)
        if snapshot[1].web.enabled:
            published.set()
            await release.wait()
        return status

    original_runtime.web_commands._apply_configuration = pause_after_publication
    running = asyncio.create_task(
        application.execute_command(
            CommandIntent(name=CommandName.WEB, arguments=("on",))
        )
    )
    await asyncio.wait_for(published.wait(), timeout=10)
    published_runtime = backend._runtime
    assert published_runtime is not None
    assert published_runtime is not original_runtime

    running.cancel("cancel Web after publication")
    release.set()
    with pytest.raises(asyncio.CancelledError) as cancelled:
        await running

    assert cancelled.value.args == ("cancel Web after publication",)
    restored_runtime = backend._runtime
    assert restored_runtime is not None
    assert restored_runtime is not published_runtime
    assert restored_runtime.application_config.web.enabled is False
    assert restored_runtime.tool_registry.resolve("web_search") is None
    assert UserConfigWriter(backend._paths.config_file).read().web.enabled is False
    assert backend._web_configuration_control.recovery_required is False
    assert (
        restored_runtime.web_commands._configuration_control
        is backend._web_configuration_control
    )

    status = await application.execute_command(
        CommandIntent(name=CommandName.WEB, arguments=("status",))
    )
    assert status.ok is True
    assert isinstance(status.value, CommandResult)
    assert isinstance(status.value.payload, WebStatusCommandPayload)
    assert status.value.payload.enabled is False
    assert status.value.payload.available is False
    assert (await application.shutdown()).ok is True


@pytest.mark.asyncio
async def test_failed_web_recovery_fences_current_and_future_runtimes(
    tmp_path: Path,
) -> None:
    application, backend = await _trusted_application(
        tmp_path,
        environ={"TAVILY_API_KEY": "test-tavily-key"},
    )
    assert (await application.initialize()).ok is True
    assert (
        await application.execute_command(CommandIntent(name=CommandName.NEW))
    ).ok is True
    original_runtime = backend._runtime
    assert original_runtime is not None
    thread_id = original_runtime.commands.current_thread_id
    assert thread_id is not None
    original_apply = original_runtime.web_commands._apply_configuration
    published = asyncio.Event()
    release = asyncio.Event()

    async def fail_recovery_after_publication(
        snapshot: ProviderConfigurationSnapshot,
    ) -> WebRuntimeStatus:
        if not snapshot[1].web.enabled:
            raise RuntimeError("private recovery failure")
        status = await original_apply(snapshot)
        published.set()
        await release.wait()
        return status

    original_runtime.web_commands._apply_configuration = (
        fail_recovery_after_publication
    )
    running = asyncio.create_task(
        application.execute_command(
            CommandIntent(name=CommandName.WEB, arguments=("on",))
        )
    )
    await asyncio.wait_for(published.wait(), timeout=10)
    running.cancel("authoritative Web cancellation")
    release.set()

    with pytest.raises(asyncio.CancelledError) as cancelled:
        await running

    assert cancelled.value.args == ("authoritative Web cancellation",)
    current_runtime = backend._runtime
    assert current_runtime is not None
    assert current_runtime.application_config.web.enabled is True
    assert UserConfigWriter(backend._paths.config_file).read().web.enabled is False
    assert backend._web_configuration_control.recovery_required is True
    assert (
        current_runtime.web_commands._configuration_control
        is backend._web_configuration_control
    )

    status = await application.execute_command(
        CommandIntent(name=CommandName.WEB, arguments=("status",))
    )
    assert status.ok is True
    assert isinstance(status.value, CommandResult)
    assert isinstance(status.value.payload, WebStatusCommandPayload)
    assert status.value.payload.enabled is True

    backend._permission_session.grant_thread_network(thread_id)
    revoked = await application.execute_command(
        CommandIntent(name=CommandName.WEB, arguments=("revoke",))
    )
    assert revoked.ok is True
    assert isinstance(revoked.value, CommandResult)
    assert backend._permission_session.thread_granted_capabilities == frozenset()

    blocked = await application.execute_command(
        CommandIntent(name=CommandName.WEB, arguments=("off",))
    )
    assert blocked.ok is True
    assert isinstance(blocked.value, CommandError)
    assert blocked.value.code == "web_configuration_recovery_required"

    future_runtime = await backend._build_workspace_runtime(
        configuration=(
            current_runtime.sources,
            current_runtime.application_config,
        ),
        selected_thread_id=thread_id,
    )
    try:
        assert (
            future_runtime.web_commands._configuration_control
            is backend._web_configuration_control
        )
        future_blocked = await future_runtime.web_commands.web(
            CommandIntent(name=CommandName.WEB, arguments=("off",))
        )
        assert isinstance(future_blocked, CommandError)
        assert future_blocked.code == "web_configuration_recovery_required"
    finally:
        await backend._close_workspace_runtime(future_runtime)

    assert (await application.shutdown()).ok is True


@pytest.mark.asyncio
async def test_candidate_cleanup_failure_preserves_primary_build_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _FailingCloseMcpManager.instances = []

    async def fail_reconcile(self: TurnCoordinator) -> tuple[object, ...]:
        del self
        raise RuntimeError("primary candidate failure")

    monkeypatch.setattr(composition, "McpManager", _FailingCloseMcpManager)
    monkeypatch.setattr(TurnCoordinator, "reconcile_startup", fail_reconcile)
    application, _ = await _trusted_application(tmp_path)

    with (
        caplog.at_level("WARNING"),
        pytest.raises(RuntimeError, match="primary candidate failure"),
    ):
        await application.initialize()

    assert _FailingCloseMcpManager.instances[0].close_calls == 1
    assert "cleanup failed during retirement" in caplog.text
    await application.shutdown()


@pytest.mark.asyncio
async def test_retirement_failure_does_not_skip_process_resource_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _FailingCloseMcpManager.instances = []
    monkeypatch.setattr(composition, "McpManager", _FailingCloseMcpManager)
    application, backend = await _trusted_application(tmp_path)
    assert (await application.initialize()).ok is True

    stopped = await application.shutdown()

    assert stopped.ok is True
    assert _FailingCloseMcpManager.instances[0].close_calls == 1
    assert backend._state_lease is None
    assert backend._workspace_path_lease is None
    assert backend._workspace_entity_lease is None


@pytest.mark.asyncio
async def test_mem0_candidate_failure_closes_provider_resources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[str] = []

    @asynccontextmanager
    async def provider_resources(*_: object, **__: object) -> AsyncIterator[Any]:
        try:
            yield cast(Any, lambda _provider, _model: object())
        finally:
            closed.append("provider")

    @asynccontextmanager
    async def fail_mem0(*_: object, **__: object) -> AsyncIterator[Any]:
        should_fail = True
        if should_fail:
            raise RuntimeError("mem0 candidate failed")
        yield object()

    monkeypatch.setattr(composition, "managed_gateway_factory", provider_resources)
    monkeypatch.setattr(composition, "managed_mem0_client", fail_mem0)
    application, backend = await _trusted_application(tmp_path)

    with pytest.raises(RuntimeError, match="mem0 candidate failed"):
        await application.initialize()

    assert closed == ["provider"]
    assert backend._runtime is None
    await application.shutdown()


@pytest.mark.asyncio
async def test_mcp_construction_failure_closes_mem0_then_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[str] = []

    @asynccontextmanager
    async def provider_resources(*_: object, **__: object) -> AsyncIterator[Any]:
        try:
            yield cast(Any, lambda _provider, _model: object())
        finally:
            closed.append("provider")

    @asynccontextmanager
    async def mem0_resources(*_: object, **__: object) -> AsyncIterator[Any]:
        try:
            yield object()
        finally:
            closed.append("mem0")

    def fail_mcp(**_: object) -> None:
        raise RuntimeError("mcp candidate failed")

    monkeypatch.setattr(composition, "managed_gateway_factory", provider_resources)
    monkeypatch.setattr(composition, "managed_mem0_client", mem0_resources)
    monkeypatch.setattr(composition, "McpManager", fail_mcp)
    application, backend = await _trusted_application(tmp_path)

    with pytest.raises(RuntimeError, match="mcp candidate failed"):
        await application.initialize()

    assert closed == ["mem0", "provider"]
    assert backend._runtime is None
    await application.shutdown()


@pytest.mark.asyncio
async def test_candidate_failure_closes_mcp_mem0_provider_in_reverse_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[str] = []

    @asynccontextmanager
    async def provider_resources(*_: object, **__: object) -> AsyncIterator[Any]:
        try:
            yield cast(Any, lambda _provider, _model: object())
        finally:
            closed.append("provider")

    @asynccontextmanager
    async def mem0_resources(*_: object, **__: object) -> AsyncIterator[Any]:
        try:
            yield object()
        finally:
            closed.append("mem0")

    class OrderedMcpManager(_TrackingMcpManager):
        instances: ClassVar[list[_TrackingMcpManager]] = []

        async def aclose(self) -> None:
            self.close_calls += 1
            closed.append("mcp")

    async def fail_reconcile(self: TurnCoordinator) -> tuple[object, ...]:
        del self
        raise RuntimeError("candidate reconcile failed")

    monkeypatch.setattr(composition, "managed_gateway_factory", provider_resources)
    monkeypatch.setattr(composition, "managed_mem0_client", mem0_resources)
    monkeypatch.setattr(composition, "McpManager", OrderedMcpManager)
    monkeypatch.setattr(TurnCoordinator, "reconcile_startup", fail_reconcile)
    application, backend = await _trusted_application(tmp_path)

    with pytest.raises(RuntimeError, match="candidate reconcile failed"):
        await application.initialize()

    assert closed == ["mcp", "mem0", "provider"]
    assert OrderedMcpManager.instances[0].close_calls == 1
    assert backend._runtime is None
    await application.shutdown()
    assert closed == ["mcp", "mem0", "provider"]


@pytest.mark.asyncio
async def test_cancelled_activation_closes_candidate_and_rolls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _TrackingMcpManager.instances = []
    _TrackingMcpManager.hang_first_close = False
    entered = asyncio.Event()

    async def block_reconcile(self: TurnCoordinator) -> tuple[object, ...]:
        del self
        entered.set()
        await asyncio.Event().wait()
        return ()

    monkeypatch.setattr(composition, "McpManager", _TrackingMcpManager)
    monkeypatch.setattr(TurnCoordinator, "reconcile_startup", block_reconcile)
    application, backend = await _trusted_application(tmp_path)
    initializing = asyncio.create_task(application.initialize())
    await asyncio.wait_for(entered.wait(), timeout=1)

    initializing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(initializing, timeout=0.5)

    candidate = _TrackingMcpManager.instances[0]
    assert candidate.close_calls == 1
    assert candidate.close_cancelled is False
    _assert_activation_rolled_back(backend)
    await application.shutdown()


@pytest.mark.asyncio
async def test_runtime_build_failure_closes_only_the_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _TrackingMcpManager.instances = []
    _TrackingMcpManager.hang_first_close = False
    monkeypatch.setattr(composition, "McpManager", _TrackingMcpManager)
    application, backend = await _trusted_application(tmp_path)
    assert (await application.initialize()).ok is True
    original = backend._runtime
    assert original is not None
    original_mcp = _TrackingMcpManager.instances[0]

    def fail_after_mcp(**_: object) -> object:
        raise RuntimeError("candidate construction failed")

    monkeypatch.setattr(
        composition,
        "load_workspace_instructions",
        fail_after_mcp,
    )

    with pytest.raises(RuntimeError, match="candidate construction failed"):
        await backend._activate()

    candidate_mcp = _TrackingMcpManager.instances[1]
    assert backend._runtime is original
    assert original_mcp.close_calls == 0
    assert candidate_mcp.close_calls == 1
    await application.shutdown()
    assert original_mcp.close_calls == 1
    assert candidate_mcp.close_calls == 1


@pytest.mark.asyncio
async def test_foreground_operation_rejects_candidate_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _TrackingMcpManager.instances = []
    _TrackingMcpManager.hang_first_close = False
    monkeypatch.setattr(composition, "McpManager", _TrackingMcpManager)
    application, backend = await _trusted_application(tmp_path)
    assert (await application.initialize()).ok is True
    original = backend._runtime
    assert original is not None
    lease = backend._foreground.acquire_operation()
    try:
        with pytest.raises(ApplicationFailure) as blocked:
            await backend._activate()
    finally:
        lease.release()

    assert blocked.value.error.code is ProductErrorCode.OPERATION_BUSY
    assert backend._runtime is original
    assert _TrackingMcpManager.instances[0].close_calls == 0
    assert _TrackingMcpManager.instances[1].close_calls == 1
    await application.shutdown()
    assert _TrackingMcpManager.instances[0].close_calls == 1


@pytest.mark.asyncio
async def test_post_publish_notification_failure_keeps_candidate_ready(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _TrackingMcpManager.instances = []
    _TrackingMcpManager.hang_first_close = False
    monkeypatch.setattr(composition, "McpManager", _TrackingMcpManager)
    application, backend = await _trusted_application(tmp_path)
    assert (await application.initialize()).ok is True
    original = backend._runtime
    assert original is not None

    async def fail_notification() -> None:
        raise RuntimeError("notification failed")

    monkeypatch.setattr(backend, "_present_next_recovery", fail_notification)
    with pytest.raises(RuntimeError, match="notification failed"):
        await backend._activate()

    candidate = backend._runtime
    assert candidate is not None
    assert candidate is not original
    assert _TrackingMcpManager.instances[0].close_calls == 1
    assert _TrackingMcpManager.instances[1].close_calls == 0
    state = await application.get_state()
    assert state.ok is True
    assert state.value is not None
    assert state.value.initialized is True
    await application.shutdown()
    assert _TrackingMcpManager.instances[0].close_calls == 1
    assert _TrackingMcpManager.instances[1].close_calls == 1


@pytest.mark.asyncio
async def test_cancelled_initial_recovery_delivery_reuses_published_runtime_and_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _BlockFirstRecoveryDelivery(CollectingEventSink):
        def __init__(self) -> None:
            super().__init__()
            self.first_started = asyncio.Event()
            self.interaction_ids: list[str] = []

        async def emit(self, event: Any) -> None:
            payload = event.payload
            if getattr(payload, "interaction_kind", None) == "recovery_decision":
                self.interaction_ids.append(payload.interaction_id)
                if len(self.interaction_ids) == 1:
                    self.first_started.set()
                    await asyncio.Event().wait()
            await super().emit(event)

    _TrackingMcpManager.instances = []
    _TrackingMcpManager.hang_first_close = False
    reconcile_calls = 0

    async def recover_once(self: TurnCoordinator) -> tuple[RecoveryResult, ...]:
        del self
        nonlocal reconcile_calls
        reconcile_calls += 1
        return (
            RecoveryResult(
                thread_id="thread_recovery",
                turn_id="turn_recovery",
                status=RecoveryStatus.INTERACTION_REQUIRED,
                error_code="tool_outcome_uncertain",
            ),
        )

    monkeypatch.setattr(composition, "McpManager", _TrackingMcpManager)
    monkeypatch.setattr(TurnCoordinator, "reconcile_startup", recover_once)
    monkeypatch.setattr(composition, "_RECOVERY_EVENT_DELIVERY_ATTEMPTS", 1)
    monkeypatch.setattr(
        composition,
        "_RECOVERY_EVENT_DELIVERY_TIMEOUT_SECONDS",
        0.01,
    )
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    await _trust_workspaces(home, workspace)
    sink = _BlockFirstRecoveryDelivery()
    application = await composition.compose_local_application(
        home=home,
        workspace=workspace,
        event_sink=sink,
        environ={},
    )
    backend = cast(composition._LocalApplicationBackend, application._backend)
    initializing = asyncio.create_task(application.initialize())
    await asyncio.wait_for(sink.first_started.wait(), timeout=1)
    published = backend._runtime
    assert published is not None
    pending = backend._interactions.pending
    assert pending is not None
    pending_id = pending.id
    assert sink.interaction_ids == [pending_id]
    assert _TrackingMcpManager.instances[0].close_calls == 0

    initializing.cancel("cancel-initial-delivery")
    with pytest.raises(asyncio.CancelledError) as cancelled:
        await asyncio.wait_for(initializing, timeout=1)

    assert isinstance(cancelled.value, asyncio.CancelledError)
    assert backend._runtime is published
    assert backend._interactions.pending is not None
    assert backend._interactions.pending.id == pending_id
    assert reconcile_calls == 1
    assert _TrackingMcpManager.instances[0].close_calls == 0

    ready = await application.initialize()
    assert ready.ok is True
    assert ready.value is not None
    assert ready.value.status.value == "ready"
    assert reconcile_calls == 1
    assert sink.interaction_ids == [pending_id, pending_id]
    assert backend._runtime is published
    await application.shutdown()
    assert _TrackingMcpManager.instances[0].close_calls == 1


@pytest.mark.asyncio
async def test_published_runtime_waits_for_bound_reader_before_old_close(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _TrackingMcpManager.instances = []
    _TrackingMcpManager.hang_first_close = False
    monkeypatch.setattr(composition, "McpManager", _TrackingMcpManager)
    application, backend = await _trusted_application(tmp_path)
    assert (await application.initialize()).ok is True
    original = backend._runtime
    assert original is not None
    entered = asyncio.Event()
    release = asyncio.Event()
    published = asyncio.Event()
    seen_by_old_request: list[composition.WorkspaceRuntime] = []
    seen_by_new_request: list[composition.WorkspaceRuntime] = []
    original_handler = original.command_dispatcher._handlers[CommandName.STATUS]

    async def pause_old_request(intent: CommandIntent) -> CommandOutcome:
        seen_by_old_request.append(backend._require_runtime())
        entered.set()
        await release.wait()
        seen_by_old_request.append(backend._require_runtime())
        return await original_handler(intent)

    original.command_dispatcher._handlers[CommandName.STATUS] = pause_old_request
    old_request = asyncio.create_task(
        application.execute_command(CommandIntent(name=CommandName.STATUS))
    )
    await asyncio.wait_for(entered.wait(), timeout=1)
    publish_runtime = backend._publish_workspace_runtime

    def capture_publication(
        candidate: composition.WorkspaceRuntime,
        *,
        expected_previous: composition.WorkspaceRuntime | None,
    ) -> None:
        publish_runtime(candidate, expected_previous=expected_previous)
        published.set()

    monkeypatch.setattr(backend, "_publish_workspace_runtime", capture_publication)
    activation = asyncio.create_task(backend._activate())
    await asyncio.wait_for(published.wait(), timeout=1)
    candidate = backend._runtime
    assert candidate is not None
    assert candidate is not original
    assert _TrackingMcpManager.instances[0].close_calls == 0
    assert activation.done() is False

    candidate_handler = candidate.command_dispatcher._handlers[CommandName.STATUS]

    async def observe_new_request(intent: CommandIntent) -> CommandOutcome:
        seen_by_new_request.append(backend._require_runtime())
        return await candidate_handler(intent)

    candidate.command_dispatcher._handlers[CommandName.STATUS] = observe_new_request
    new_request = await application.execute_command(
        CommandIntent(name=CommandName.STATUS)
    )
    assert new_request.ok is True
    assert seen_by_new_request == [candidate]
    assert _TrackingMcpManager.instances[0].close_calls == 0

    release.set()
    assert (await old_request).ok is True
    await asyncio.wait_for(activation, timeout=1)
    assert seen_by_old_request == [original, original]
    assert _TrackingMcpManager.instances[0].close_calls == 1
    assert backend._request_runtime.get() is None
    assert original.resources.reader_count == 0
    await application.shutdown()
    assert _TrackingMcpManager.instances[0].close_calls == 1
    assert _TrackingMcpManager.instances[1].close_calls == 1


@pytest.mark.asyncio
async def test_runtime_request_scope_resets_after_exception_and_cancellation(
    tmp_path: Path,
) -> None:
    application, backend = await _trusted_application(tmp_path)
    assert (await application.initialize()).ok is True
    runtime = backend._runtime
    assert runtime is not None

    async def fail_request(_: CommandIntent) -> CommandOutcome:
        assert backend._require_runtime() is runtime
        raise RuntimeError("request failed")

    runtime.command_dispatcher._handlers[CommandName.STATUS] = fail_request
    with pytest.raises(RuntimeError, match="request failed"):
        await backend.run_command(CommandIntent(name=CommandName.STATUS))
    assert backend._request_runtime.get() is None
    assert runtime.resources.reader_count == 0

    entered = asyncio.Event()

    async def cancel_request(_: CommandIntent) -> CommandOutcome:
        assert backend._require_runtime() is runtime
        entered.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    runtime.command_dispatcher._handlers[CommandName.STATUS] = cancel_request
    request = asyncio.create_task(
        backend.run_command(CommandIntent(name=CommandName.STATUS))
    )
    await asyncio.wait_for(entered.wait(), timeout=1)
    request.cancel("request-cancelled")
    with pytest.raises(asyncio.CancelledError) as cancelled:
        await request
    assert cancelled.value.args == ("request-cancelled",)
    assert backend._request_runtime.get() is None
    assert runtime.resources.reader_count == 0
    await application.shutdown()


@pytest.mark.asyncio
async def test_shutdown_releases_bootstrap_lock_before_reader_drain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application, backend = await _trusted_application(tmp_path)
    assert (await application.initialize()).ok is True
    request_entered = asyncio.Event()
    queue_request = asyncio.Event()

    async def resolve_after_bootstrap(
        interaction_id: str,
        decision: str,
        *,
        runtime: composition.WorkspaceRuntime | None,
    ) -> InteractionResult:
        del interaction_id, decision
        assert runtime is backend._runtime
        request_entered.set()
        await queue_request.wait()
        async with backend._bootstrap_lock:
            return InteractionResult(accepted=False, status="not_found")

    monkeypatch.setattr(
        backend,
        "_resolve_interaction_in_runtime",
        resolve_after_bootstrap,
    )
    await backend._bootstrap_lock.acquire()
    try:
        request = asyncio.create_task(
            backend.resolve_interaction("interaction_missing", "deny")
        )
        await asyncio.wait_for(request_entered.wait(), timeout=1)
        shutdown = asyncio.create_task(application.shutdown())
        while not backend._foreground.closing:
            await asyncio.sleep(0)
        await asyncio.sleep(0)
        queue_request.set()
        await asyncio.sleep(0)
    finally:
        backend._bootstrap_lock.release()

    resolved = await asyncio.wait_for(request, timeout=1)
    stopped = await asyncio.wait_for(shutdown, timeout=1)
    assert resolved.status == "not_found"
    assert stopped.ok is True
    assert backend._runtime is None


@pytest.mark.asyncio
async def test_cancelled_shutdown_finishes_resource_and_process_cleanup_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _TrackingMcpManager.instances = []
    _TrackingMcpManager.hang_first_close = False
    monkeypatch.setattr(composition, "McpManager", _TrackingMcpManager)
    application, backend = await _trusted_application(tmp_path)
    assert (await application.initialize()).ok is True
    runtime = backend._runtime
    assert runtime is not None
    entered = asyncio.Event()
    release = asyncio.Event()
    original_handler = runtime.command_dispatcher._handlers[CommandName.STATUS]

    async def pause_request(intent: CommandIntent) -> CommandOutcome:
        entered.set()
        await release.wait()
        return await original_handler(intent)

    runtime.command_dispatcher._handlers[CommandName.STATUS] = pause_request
    request = asyncio.create_task(
        application.execute_command(CommandIntent(name=CommandName.STATUS))
    )
    await asyncio.wait_for(entered.wait(), timeout=1)
    shutdown = asyncio.create_task(application.shutdown())
    while backend._runtime is not None:
        await asyncio.sleep(0)

    shutdown.cancel("cancel-shutdown-caller")
    await asyncio.sleep(0)
    assert shutdown.done() is False
    release.set()
    assert (await request).ok is True

    with pytest.raises(asyncio.CancelledError) as cancelled:
        await asyncio.wait_for(shutdown, timeout=1)
    assert cancelled.value.args == ("cancel-shutdown-caller",)
    assert backend._closed is True
    assert runtime.resources.closed is True
    assert _TrackingMcpManager.instances[0].close_calls == 1
    assert backend._state_lease is None
    assert backend._workspace_path_lease is None
    assert backend._workspace_entity_lease is None

    assert (await application.shutdown()).ok is True
    assert _TrackingMcpManager.instances[0].close_calls == 1


@pytest.mark.asyncio
async def test_shutdown_prevents_cancelled_provider_child_from_publishing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _TrackingMcpManager.instances = []
    _TrackingMcpManager.hang_first_close = False
    monkeypatch.setattr(composition, "McpManager", _TrackingMcpManager)
    application, backend = await _trusted_application(tmp_path)
    assert (await application.initialize()).ok is True
    original = backend._runtime
    assert original is not None
    provider_configuration = original.provider_configuration
    candidate_build_entered = asyncio.Event()
    release_candidate_build = asyncio.Event()
    build_runtime = backend._build_workspace_runtime

    async def block_provider_candidate(
        *,
        configuration: ProviderConfigurationSnapshot | None = None,
        selected_thread_id: str | None = None,
    ) -> composition.WorkspaceRuntime:
        if configuration is not None:
            candidate_build_entered.set()
            await release_candidate_build.wait()
        return await build_runtime(
            configuration=configuration,
            selected_thread_id=selected_thread_id,
        )

    monkeypatch.setattr(
        backend,
        "_build_workspace_runtime",
        block_provider_candidate,
    )
    saving = asyncio.create_task(
        application.set_provider_credential(
            ProviderCredentialSetRequest(
                provider="mem0",
                action="add",
                api_key=SecretStr("persisted-during-shutdown"),
            )
        )
    )
    await asyncio.wait_for(candidate_build_entered.wait(), timeout=1)
    shutdown = asyncio.create_task(application.shutdown())
    while not backend._foreground.closing:
        await asyncio.sleep(0)
    release_candidate_build.set()

    with pytest.raises(asyncio.CancelledError):
        await saving
    stopped = await asyncio.wait_for(shutdown, timeout=1)
    assert stopped.ok is True
    assert backend._runtime is None
    assert len(_TrackingMcpManager.instances) == 1
    assert all(manager.close_calls == 1 for manager in _TrackingMcpManager.instances)
    with pytest.raises(ProviderConfigurationRecoveryRequired) as fenced:
        provider_configuration.require_consistent()
    assert "runtime_publish" in {stage for stage, _ in fenced.value.recovery_failures}


@pytest.mark.asyncio
async def test_repeated_runtime_rebuilds_release_close_registrations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _TrackingMcpManager.instances = []
    _TrackingMcpManager.hang_first_close = False
    monkeypatch.setattr(composition, "McpManager", _TrackingMcpManager)
    application, backend = await _trusted_application(tmp_path)
    assert (await application.initialize()).ok is True
    runtime_generations: list[composition.WorkspaceRuntime] = []
    assert backend._runtime is not None
    runtime_generations.append(backend._runtime)

    for generation in range(1, 6):
        await backend._activate()
        assert backend._runtime is not None
        runtime_generations.append(backend._runtime)
        assert len(_TrackingMcpManager.instances) == generation + 1
        assert all(
            manager.close_calls == 1 for manager in _TrackingMcpManager.instances[:-1]
        )
        assert _TrackingMcpManager.instances[-1].close_calls == 0
        assert len(backend._runtime_retirements) == 0
        assert all(runtime.resources.closed for runtime in runtime_generations[:-1])

    await application.shutdown()
    assert all(manager.close_calls == 1 for manager in _TrackingMcpManager.instances)
    assert all(runtime.resources.closed for runtime in runtime_generations)
    assert len(backend._runtime_retirements) == 0


@pytest.mark.asyncio
async def test_candidate_resource_tasks_do_not_inherit_request_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application, backend = await _trusted_application(tmp_path)
    backend._paths.config_file.parent.mkdir(parents=True, exist_ok=True)
    backend._paths.config_file.write_text(
        "mcp_servers:\n  - id: fake\n    command: fake\n    enabled: true\n",
        encoding="utf-8",
    )
    observed_contexts: list[composition.WorkspaceRuntime | None] = []

    class _ContextCapturingMcpManager(_TrackingMcpManager):
        def __init__(self, **kwargs: object) -> None:
            super().__init__(**kwargs)
            self._background: asyncio.Task[None] | None = None

        async def start_enabled(self) -> None:
            started = asyncio.Event()

            async def long_lived_resource() -> None:
                observed_contexts.append(backend._request_runtime.get())
                started.set()
                await asyncio.Event().wait()

            self._background = asyncio.create_task(long_lived_resource())
            await started.wait()

        async def aclose(self) -> None:
            await super().aclose()
            background = self._background
            if background is None:
                return
            background.cancel()
            with suppress(asyncio.CancelledError):
                await background

    _ContextCapturingMcpManager.instances = []
    _ContextCapturingMcpManager.hang_first_close = False
    monkeypatch.setattr(
        composition,
        "McpManager",
        _ContextCapturingMcpManager,
    )
    assert (await application.initialize()).ok is True
    old_runtime = backend._runtime
    assert old_runtime is not None
    old_commands_ref = weakref.ref(old_runtime.commands)

    configured = await application.set_provider_credential(
        ProviderCredentialSetRequest(
            provider="mem0",
            action="delete",
        )
    )
    assert configured.ok is True
    assert backend._runtime is not old_runtime
    assert observed_contexts == [None, None]
    for _ in range(20):
        if (
            _ContextCapturingMcpManager.instances[0].close_calls == 1
            and len(backend._runtime_retirements) == 0
        ):
            break
        await asyncio.sleep(0)
    assert _ContextCapturingMcpManager.instances[0].close_calls == 1
    assert len(backend._runtime_retirements) == 0
    del old_runtime

    for _ in range(10):
        gc.collect()
        await asyncio.sleep(0)
        if old_commands_ref() is None:
            break
    assert old_commands_ref() is None
    await application.shutdown()
    assert all(
        manager.close_calls == 1 for manager in _ContextCapturingMcpManager.instances
    )
