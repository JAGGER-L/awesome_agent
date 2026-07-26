from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Literal

import pytest
from dotenv import dotenv_values
from pydantic import SecretStr

import awesome_agent.application.provider_configuration as provider_configuration
from awesome_agent.application.command_results import (
    CommandError,
    CommandInteractionResult,
    CommandResult,
    ModelCommandPayload,
    NoticeCommandPayload,
)
from awesome_agent.application.commands import CommandIntent, CommandName
from awesome_agent.application.contracts import (
    ProviderCredentialSetRequest,
    ProviderCredentialSetResult,
    ProviderCredentialSetStatus,
)
from awesome_agent.application.provider_configuration import (
    ProviderConfigurationRecoveryRequired,
    ProviderConfigurationService,
    reconcile_provider_credential_transaction,
)
from awesome_agent.config import (
    ApplicationConfig,
    CredentialService,
    CredentialSource,
    CredentialValidation,
    CredentialValidationStatus,
    LoadedConfigSources,
    ProviderCredentialTransactionPhase,
    ProviderCredentialTransactionRecord,
    SecretFileSnapshot,
    UserConfigWriter,
    UserSecretStore,
    load_config_sources,
    resolve_application_config,
)
from awesome_agent.config.credential_transaction import (
    ProviderCredentialTransactionJournal,
)
from awesome_agent.config.model_transaction import ProviderModelTransactionJournal
from awesome_agent.config.resource_lock import exclusive_resource_lock
from awesome_agent.conversation import ConversationService, ThreadNotFound
from awesome_agent.core.cancellation import run_cancellation_safe_blocking_call
from awesome_agent.paths import AwesomePaths
from awesome_agent.storage.conversations import SQLiteConversationRepositories


class FakeValidator:
    def __init__(self, status: CredentialValidationStatus) -> None:
        self.status = status
        self.calls: list[tuple[str, str]] = []

    async def validate(
        self,
        provider: str,
        api_key: SecretStr,
        *,
        kimi_region: object,
    ) -> CredentialValidation:
        del kimi_region
        self.calls.append((provider, api_key.get_secret_value()))
        code = {
            CredentialValidationStatus.VALID: "credential_valid",
            CredentialValidationStatus.INVALID: "credential_invalid",
            CredentialValidationStatus.UNVERIFIED: "credential_validation_unavailable",
        }[self.status]
        return CredentialValidation(status=self.status, code=code)


def _service(
    tmp_path: Path,
    *,
    validator: FakeValidator,
    environ: Mapping[str, str] | None = None,
    runtime: list[tuple[LoadedConfigSources, ApplicationConfig]] | None = None,
) -> tuple[
    ProviderConfigurationService,
    ConversationService,
    Callable[[], LoadedConfigSources],
]:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    paths = AwesomePaths.from_home(home)
    environment = dict(environ or {})
    conversation = ConversationService(
        store=SQLiteConversationRepositories(tmp_path / "application.db")
    )

    def sources() -> LoadedConfigSources:
        return load_config_sources(
            paths=paths,
            workspace=workspace,
            workspace_trusted=True,
            environ=environment,
        )

    def load_configuration() -> tuple[LoadedConfigSources, ApplicationConfig]:
        loaded = sources()
        return loaded, resolve_application_config(loaded)

    if runtime is not None:
        runtime.append(load_configuration())

    def apply_configuration(
        snapshot: tuple[LoadedConfigSources, ApplicationConfig],
    ) -> None:
        if runtime is not None:
            runtime[0] = snapshot

    service = ProviderConfigurationService(
        conversation=conversation,
        config_writer=UserConfigWriter(paths.config_file),
        secret_store=UserSecretStore(paths.env_file),
        validator=validator,
        sources=sources,
        load_configuration=load_configuration,
        apply_configuration=apply_configuration,
        model_transaction_journal=ProviderModelTransactionJournal(
            paths.provider_model_transaction_file
        ),
        credential_transaction_journal=ProviderCredentialTransactionJournal(
            paths.provider_credential_transaction_file,
            paths.provider_credential_backup_file,
        ),
    )
    return service, conversation, sources


def _hold_resource_lock(path: Path, entered: threading.Event, seconds: float) -> None:
    with exclusive_resource_lock(path):
        entered.set()
        time.sleep(seconds)


def test_credential_request_rejects_nul_before_provider_validation() -> None:
    with pytest.raises(ValueError, match="Provider credential value is invalid"):
        ProviderCredentialSetRequest(
            provider="mem0",
            action="add",
            api_key=SecretStr("must-not-write\0suffix"),
        )


class _BlockingSetSecretStore(UserSecretStore):
    def __init__(
        self,
        path: Path,
        *,
        secret_written: threading.Event,
        release_transaction: threading.Event,
    ) -> None:
        super().__init__(path)
        self._secret_written = secret_written
        self._release_transaction = release_transaction

    def set(self, name: str, value: SecretStr) -> None:
        super().set(name, value)
        self._secret_written.set()
        if not self._release_transaction.wait(2.0):
            raise AssertionError("Credential transaction release was not signalled.")


class _LateCompletingSetSecretStore(UserSecretStore):
    def __init__(
        self,
        path: Path,
        *,
        secret_written: threading.Event,
        release_transaction: threading.Event,
        transaction_completed: threading.Event,
    ) -> None:
        super().__init__(path)
        self._secret_written = secret_written
        self._release_transaction = release_transaction
        self._transaction_completed = transaction_completed

    def set(self, name: str, value: SecretStr) -> None:
        super().set(name, value)
        self._secret_written.set()
        if not self._release_transaction.wait(2.0):
            raise AssertionError("Credential transaction release was not signalled.")
        self._transaction_completed.set()


class _ObservingDeleteSecretStore(UserSecretStore):
    def __init__(self, path: Path, *, delete_entered: threading.Event) -> None:
        super().__init__(path)
        self._delete_entered = delete_entered

    def delete(self, name: str) -> bool:
        self._delete_entered.set()
        return super().delete(name)


def _service_with_secret_store(
    tmp_path: Path,
    secret_store: UserSecretStore,
) -> ProviderConfigurationService:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    paths = AwesomePaths.from_home(home)

    def sources() -> LoadedConfigSources:
        return load_config_sources(
            paths=paths,
            workspace=workspace,
            workspace_trusted=True,
            environ={},
        )

    def load_configuration() -> tuple[LoadedConfigSources, ApplicationConfig]:
        loaded = sources()
        return loaded, resolve_application_config(loaded)

    return ProviderConfigurationService(
        conversation=ConversationService(
            store=SQLiteConversationRepositories(tmp_path / "application.db")
        ),
        config_writer=UserConfigWriter(paths.config_file),
        secret_store=secret_store,
        validator=FakeValidator(CredentialValidationStatus.VALID),
        sources=sources,
        load_configuration=load_configuration,
        apply_configuration=lambda _: None,
        model_transaction_journal=ProviderModelTransactionJournal(
            paths.provider_model_transaction_file
        ),
        credential_transaction_journal=ProviderCredentialTransactionJournal(
            paths.provider_credential_transaction_file,
            paths.provider_credential_backup_file,
        ),
    )


@pytest.mark.asyncio
async def test_model_selects_provider_before_model_and_bridges_missing_auth(
    tmp_path: Path,
) -> None:
    service, conversation, _ = _service(
        tmp_path,
        validator=FakeValidator(CredentialValidationStatus.VALID),
    )
    thread = conversation.create_thread("workspace_1")

    providers = await service.model_command(
        CommandIntent(name=CommandName.MODEL),
        thread_id=thread.id,
    )
    missing = await service.model_command(
        CommandIntent(name=CommandName.MODEL, arguments=("deepseek",)),
        thread_id=thread.id,
    )

    assert isinstance(providers, CommandInteractionResult)
    assert providers.interaction.kind == "selection"
    assert [option.value for option in providers.interaction.options] == [
        "deepseek",
        "kimi",
    ]
    assert [option.description for option in providers.interaction.options] == [
        "Not configured",
        "Not configured",
    ]
    assert isinstance(missing, CommandInteractionResult)
    assert missing.interaction.kind == "secret"
    assert missing.interaction.provider == "deepseek"
    assert missing.interaction.action == "add"


@pytest.mark.asyncio
async def test_valid_credential_enables_provider_model_selection(
    tmp_path: Path,
) -> None:
    validator = FakeValidator(CredentialValidationStatus.VALID)
    service, conversation, sources = _service(tmp_path, validator=validator)
    thread = conversation.create_thread("workspace_1")

    saved = await service.set_credential(
        ProviderCredentialSetRequest(
            provider="deepseek",
            action="add",
            api_key=SecretStr("new-secret"),
        )
    )
    models = await service.model_command(
        CommandIntent(name=CommandName.MODEL, arguments=("deepseek",)),
        thread_id=thread.id,
    )

    assert saved.status is ProviderCredentialSetStatus.CONFIGURED
    assert saved.source is CredentialSource.AWESOME
    assert sources().provider_credentials.deepseek.configured is True
    assert isinstance(models, CommandInteractionResult)
    assert models.interaction.kind == "selection"
    assert [option.value for option in models.interaction.options] == [
        "deepseek/deepseek-v4-flash",
        "deepseek/deepseek-v4-pro",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider", "action", "api_key"),
    [
        ("mem0", "add", "new-mem0-secret"),
        ("deepseek", "replace", "replacement-secret"),
        ("deepseek", "delete", None),
    ],
)
async def test_credential_second_step_failure_restores_full_env_and_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider: CredentialService,
    action: Literal["add", "replace", "delete"],
    api_key: str | None,
) -> None:
    runtime: list[tuple[LoadedConfigSources, ApplicationConfig]] = []
    service, _, _ = _service(
        tmp_path,
        validator=FakeValidator(CredentialValidationStatus.VALID),
        runtime=runtime,
    )
    paths = AwesomePaths.from_home(tmp_path / "home")
    previous_env = (
        b"# retain comment\n"
        b"DEEPSEEK_API_KEY=old-deepseek-secret\n"
        b"MOONSHOT_API_KEY=unrelated-secret\n"
    )
    paths.home.mkdir(parents=True, exist_ok=True)
    paths.env_file.write_bytes(previous_env)
    previous_config = service._config_writer.read()
    initial_runtime = runtime[0]
    primary = OSError("injected credential source persistence failure")

    def fail_source(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise primary

    monkeypatch.setattr(service, "_write_source", fail_source)
    request = ProviderCredentialSetRequest(
        provider=provider,
        action=action,
        api_key=None if api_key is None else SecretStr(api_key),
    )

    with pytest.raises(OSError) as raised:
        await service.set_credential(request)

    assert raised.value is primary
    assert paths.env_file.read_bytes() == previous_env
    assert service._config_writer.read() == previous_config
    assert runtime[0] is initial_runtime
    service._credential_transaction_journal.require_clean()


@pytest.mark.asyncio
async def test_credential_post_commit_cleanup_failure_still_applies_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime: list[tuple[LoadedConfigSources, ApplicationConfig]] = []
    service, _, _ = _service(
        tmp_path,
        validator=FakeValidator(CredentialValidationStatus.VALID),
        runtime=runtime,
    )
    journal = service._credential_transaction_journal
    real_clear = journal.clear

    def clear_then_fail(record: ProviderCredentialTransactionRecord) -> None:
        real_clear(record)
        raise OSError("injected post-unlink directory sync failure")

    monkeypatch.setattr(journal, "clear", clear_then_fail)

    saved = await service.set_credential(
        ProviderCredentialSetRequest(
            provider="mem0",
            action="add",
            api_key=SecretStr("committed-secret"),
        )
    )

    assert saved.status is ProviderCredentialSetStatus.CONFIGURED
    assert runtime[0][0].secrets.mem0_api_key is not None
    assert runtime[0][0].secrets.mem0_api_key.get_secret_value() == "committed-secret"
    journal.require_clean()


@pytest.mark.asyncio
async def test_abandoned_credential_worker_fences_late_commit_until_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = AwesomePaths.from_home(tmp_path / "home")
    secret_written = threading.Event()
    release_transaction = threading.Event()
    transaction_completed = threading.Event()
    service = _service_with_secret_store(
        tmp_path,
        _LateCompletingSetSecretStore(
            paths.env_file,
            secret_written=secret_written,
            release_transaction=release_transaction,
            transaction_completed=transaction_completed,
        ),
    )
    applied: list[tuple[LoadedConfigSources, ApplicationConfig]] = []
    monkeypatch.setattr(service, "_apply_configuration", applied.append)

    async def short_cleanup(
        call: Callable[[], object],
        *,
        on_completed: Callable[[object], None] | None = None,
        on_abandoned: Callable[[], None] | None = None,
    ) -> object:
        return await run_cancellation_safe_blocking_call(
            call,
            on_completed=on_completed,
            on_abandoned=on_abandoned,
            cleanup_timeout_seconds=0.02,
        )

    monkeypatch.setattr(
        provider_configuration,
        "run_cancellation_safe_blocking_call",
        short_cleanup,
    )
    operation = asyncio.create_task(
        service.set_credential(
            ProviderCredentialSetRequest(
                provider="mem0",
                action="add",
                api_key=SecretStr("late-secret"),
            )
        )
    )
    try:
        assert await asyncio.to_thread(secret_written.wait, 1.0)
        operation.cancel("shutdown")
        with pytest.raises(asyncio.CancelledError) as cancelled:
            await operation
        assert cancelled.value.args == ("shutdown",)
    finally:
        release_transaction.set()

    assert await asyncio.to_thread(transaction_completed.wait, 1.0)
    deadline = asyncio.get_running_loop().time() + 1.0
    while (
        paths.provider_credential_transaction_file.exists()
        or paths.provider_credential_backup_file.exists()
    ):
        assert asyncio.get_running_loop().time() < deadline
        await asyncio.sleep(0.01)

    service._credential_transaction_journal.require_clean()
    assert applied == []
    with pytest.raises(ProviderConfigurationRecoveryRequired) as fenced:
        service.require_consistent()
    assert "cancellation_cleanup_abandoned" in {
        stage for stage, _ in fenced.value.recovery_failures
    }


@pytest.mark.asyncio
async def test_abandoned_model_worker_fences_late_commit_until_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime: list[tuple[LoadedConfigSources, ApplicationConfig]] = []
    service, conversation, _ = _service(
        tmp_path,
        validator=FakeValidator(CredentialValidationStatus.VALID),
        environ={"DEEPSEEK_API_KEY": "environment-secret"},
        runtime=runtime,
    )
    thread = conversation.create_thread(
        "workspace_1",
        current_model="deepseek/deepseek-v4-flash",
    )
    initial_runtime = runtime[0]
    model_written = threading.Event()
    release_transaction = threading.Event()
    transaction_completed = threading.Event()
    real_set_model = conversation.set_model

    def block_after_model_write(thread_id: str, model: str | None) -> object:
        updated = real_set_model(thread_id, model)
        model_written.set()
        if not release_transaction.wait(2.0):
            raise AssertionError("Model transaction release was not signalled.")
        transaction_completed.set()
        return updated

    monkeypatch.setattr(conversation, "set_model", block_after_model_write)

    async def short_cleanup(
        call: Callable[[], object],
        *,
        on_completed: Callable[[object], None] | None = None,
        on_abandoned: Callable[[], None] | None = None,
    ) -> object:
        return await run_cancellation_safe_blocking_call(
            call,
            on_completed=on_completed,
            on_abandoned=on_abandoned,
            cleanup_timeout_seconds=0.02,
        )

    monkeypatch.setattr(
        provider_configuration,
        "run_cancellation_safe_blocking_call",
        short_cleanup,
    )
    operation = asyncio.create_task(
        service.model_command(
            CommandIntent(
                name=CommandName.MODEL,
                arguments=("deepseek", "deepseek/deepseek-v4-pro"),
            ),
            thread_id=thread.id,
        )
    )
    try:
        assert await asyncio.to_thread(model_written.wait, 1.0)
        operation.cancel("shutdown")
        with pytest.raises(asyncio.CancelledError):
            await operation
    finally:
        release_transaction.set()

    assert await asyncio.to_thread(transaction_completed.wait, 1.0)
    deadline = asyncio.get_running_loop().time() + 1.0
    while service._model_transaction_journal.read() is not None:
        assert asyncio.get_running_loop().time() < deadline
        await asyncio.sleep(0.01)

    assert runtime[0] is initial_runtime
    assert (
        conversation.read_thread(thread.id).thread.current_model
        == "deepseek/deepseek-v4-pro"
    )
    with pytest.raises(ProviderConfigurationRecoveryRequired) as fenced:
        service.require_consistent()
    assert "cancellation_cleanup_abandoned" in {
        stage for stage, _ in fenced.value.recovery_failures
    }


@pytest.mark.parametrize(
    "phase",
    [
        ProviderCredentialTransactionPhase.PREPARED,
        ProviderCredentialTransactionPhase.SECRET_COMMITTED,
        ProviderCredentialTransactionPhase.COMMITTED,
    ],
)
def test_credential_startup_recovery_rolls_back_before_commit_and_forwards_commit(
    tmp_path: Path,
    phase: ProviderCredentialTransactionPhase,
) -> None:
    paths = AwesomePaths.from_home(tmp_path / "home")
    paths.home.mkdir(parents=True)
    previous_env = SecretFileSnapshot(
        existed=True,
        content=(
            b"# preserve\n"
            b"DEEPSEEK_API_KEY=old-secret\n"
            b"MOONSHOT_API_KEY=unrelated-secret\n"
        ),
    )
    paths.env_file.write_bytes(previous_env.content)
    store = UserSecretStore(paths.env_file)
    target_env = store.plan_set("DEEPSEEK_API_KEY", SecretStr("new-secret"))
    writer = UserConfigWriter(paths.config_file)
    journal = ProviderCredentialTransactionJournal(
        paths.provider_credential_transaction_file,
        paths.provider_credential_backup_file,
    )
    prepared = ProviderCredentialTransactionRecord(
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
    current = journal.prepare(prepared)
    store.restore(target_env)
    if phase in {
        ProviderCredentialTransactionPhase.SECRET_COMMITTED,
        ProviderCredentialTransactionPhase.COMMITTED,
    }:
        current = journal.mark_secret_committed(current)
    if phase is ProviderCredentialTransactionPhase.COMMITTED:
        writer.update(
            lambda document: document.model_copy(
                update={
                    "credentials": document.credentials.model_copy(
                        update={"deepseek": CredentialSource.AWESOME}
                    )
                }
            )
        )
        current = journal.mark_committed(current)

    assert reconcile_provider_credential_transaction(
        journal=journal,
        config_writer=writer,
        secret_store=store,
    )

    use_target = phase is ProviderCredentialTransactionPhase.COMMITTED
    assert store.snapshot() == (target_env if use_target else previous_env)
    assert writer.read().credentials.deepseek is (
        CredentialSource.AWESOME if use_target else CredentialSource.ENVIRONMENT
    )
    journal.require_clean()


@pytest.mark.asyncio
async def test_credential_secret_and_source_updates_share_one_transaction_lock(
    tmp_path: Path,
) -> None:
    paths = AwesomePaths.from_home(tmp_path / "home")
    secret_written = threading.Event()
    release_transaction = threading.Event()
    delete_entered = threading.Event()
    adding = _service_with_secret_store(
        tmp_path,
        _BlockingSetSecretStore(
            paths.env_file,
            secret_written=secret_written,
            release_transaction=release_transaction,
        ),
    )
    deleting = _service_with_secret_store(
        tmp_path,
        _ObservingDeleteSecretStore(
            paths.env_file,
            delete_entered=delete_entered,
        ),
    )

    add_task = asyncio.create_task(
        adding.set_credential(
            ProviderCredentialSetRequest(
                provider="mem0",
                action="add",
                api_key=SecretStr("mem0-secret"),
            )
        )
    )
    delete_task: asyncio.Task[ProviderCredentialSetResult] | None = None
    try:
        assert await asyncio.to_thread(secret_written.wait, 1.0)
        delete_task = asyncio.create_task(
            deleting.set_credential(
                ProviderCredentialSetRequest(provider="mem0", action="delete")
            )
        )

        assert not await asyncio.to_thread(delete_entered.wait, 0.2)
    finally:
        release_transaction.set()
        pending = [add_task]
        if delete_task is not None:
            pending.append(delete_task)
        await asyncio.gather(*pending, return_exceptions=True)

    assert delete_entered.is_set()


@pytest.mark.asyncio
async def test_invalid_replacement_preserves_the_existing_secret(
    tmp_path: Path,
) -> None:
    validator = FakeValidator(CredentialValidationStatus.INVALID)
    service, _, sources = _service(tmp_path, validator=validator)
    UserSecretStore(tmp_path / "home" / ".env").set(
        "DEEPSEEK_API_KEY",
        SecretStr("old-secret"),
    )

    result = await service.set_credential(
        ProviderCredentialSetRequest(
            provider="deepseek",
            action="replace",
            api_key=SecretStr("invalid-secret"),
        )
    )

    assert result.status is ProviderCredentialSetStatus.INVALID
    assert dotenv_values(tmp_path / "home" / ".env")["DEEPSEEK_API_KEY"] == (
        "old-secret"
    )
    assert sources().provider_credentials.deepseek.configured is True


@pytest.mark.asyncio
async def test_unverified_credential_requires_explicit_save_anyway(
    tmp_path: Path,
) -> None:
    validator = FakeValidator(CredentialValidationStatus.UNVERIFIED)
    service, _, _ = _service(tmp_path, validator=validator)
    request = ProviderCredentialSetRequest(
        provider="kimi",
        action="add",
        api_key=SecretStr("unverified-secret"),
    )

    confirmation = await service.set_credential(request)
    assert confirmation.status is ProviderCredentialSetStatus.CONFIRM_UNVERIFIED
    assert not (tmp_path / "home" / ".env").exists()

    saved = await service.set_credential(
        request.model_copy(update={"allow_unverified": True})
    )
    assert saved.status is ProviderCredentialSetStatus.CONFIGURED
    assert dotenv_values(tmp_path / "home" / ".env")["MOONSHOT_API_KEY"] == (
        "unverified-secret"
    )


@pytest.mark.asyncio
async def test_awesome_credentials_can_be_managed_alongside_environment(
    tmp_path: Path,
) -> None:
    service, _, _ = _service(
        tmp_path,
        validator=FakeValidator(CredentialValidationStatus.VALID),
        environ={"DEEPSEEK_API_KEY": "external-secret"},
    )

    replacement = await service.set_credential(
        ProviderCredentialSetRequest(
            provider="deepseek",
            action="replace",
            api_key=SecretStr("replacement"),
        )
    )
    deletion = await service.set_credential(
        ProviderCredentialSetRequest(
            provider="deepseek",
            action="delete",
        )
    )

    assert replacement.status is ProviderCredentialSetStatus.CONFIGURED
    assert replacement.source is CredentialSource.AWESOME
    assert deletion.status is ProviderCredentialSetStatus.DELETED


@pytest.mark.asyncio
async def test_auth_exposes_both_sources_and_persists_explicit_selection(
    tmp_path: Path,
) -> None:
    service, _, sources = _service(
        tmp_path,
        validator=FakeValidator(CredentialValidationStatus.VALID),
        environ={"DEEPSEEK_API_KEY": "environment-secret"},
    )
    UserSecretStore(tmp_path / "home" / ".env").set(
        "DEEPSEEK_API_KEY", SecretStr("awesome-secret")
    )

    picker = await service.auth_command(
        CommandIntent(name=CommandName.AUTH, arguments=("deepseek",))
    )
    selected = await service.auth_command(
        CommandIntent(
            name=CommandName.AUTH,
            arguments=("deepseek", "awesome", "use"),
        )
    )

    assert isinstance(picker, CommandInteractionResult)
    assert picker.interaction.kind == "selection"
    assert [(item.value, item.disabled) for item in picker.interaction.options] == [
        ("environment", False),
        ("awesome", False),
    ]
    assert isinstance(selected, CommandResult)
    assert isinstance(selected.payload, NoticeCommandPayload)
    assert (
        sources().provider_credentials.deepseek.selected_source
        is CredentialSource.AWESOME
    )
    key = sources().secrets.deepseek_api_key
    assert key is not None
    assert key.get_secret_value() == "awesome-secret"


@pytest.mark.asyncio
async def test_auth_config_lock_wait_keeps_event_loop_schedulable(
    tmp_path: Path,
) -> None:
    service, _, sources = _service(
        tmp_path,
        validator=FakeValidator(CredentialValidationStatus.VALID),
        environ={"DEEPSEEK_API_KEY": "environment-secret"},
    )
    config_path = AwesomePaths.from_home(tmp_path / "home").config_file
    entered = threading.Event()
    holder = threading.Thread(
        target=_hold_resource_lock,
        args=(config_path, entered, 0.4),
        daemon=True,
    )
    holder.start()
    assert await asyncio.to_thread(entered.wait, 1.0)

    command = asyncio.create_task(
        service.auth_command(
            CommandIntent(
                name=CommandName.AUTH,
                arguments=("deepseek", "environment"),
            )
        )
    )
    heartbeat = asyncio.create_task(asyncio.sleep(0.05))
    try:
        await asyncio.wait_for(heartbeat, timeout=0.2)
        assert not command.done()
        outcome = await command
    finally:
        await asyncio.to_thread(holder.join, 1.0)

    assert isinstance(outcome, CommandResult)
    assert (
        sources().provider_credentials.deepseek.selected_source
        is CredentialSource.ENVIRONMENT
    )


@pytest.mark.asyncio
async def test_cancelled_auth_lock_wait_finishes_transaction_without_blocking_loop(
    tmp_path: Path,
) -> None:
    paths = AwesomePaths.from_home(tmp_path / "home")
    UserSecretStore(paths.env_file).set(
        "DEEPSEEK_API_KEY",
        SecretStr("awesome-secret"),
    )
    UserConfigWriter(paths.config_file).update(
        lambda current: current.model_copy(
            update={
                "credentials": current.credentials.model_copy(
                    update={"deepseek": CredentialSource.AWESOME}
                )
            }
        )
    )
    runtime: list[tuple[LoadedConfigSources, ApplicationConfig]] = []
    service, _, sources = _service(
        tmp_path,
        validator=FakeValidator(CredentialValidationStatus.VALID),
        environ={"DEEPSEEK_API_KEY": "environment-secret"},
        runtime=runtime,
    )
    initial_runtime = runtime[0]
    assert (
        initial_runtime[0].provider_credentials.deepseek.selected_source
        is CredentialSource.AWESOME
    )
    config_path = paths.config_file
    entered = threading.Event()
    holder = threading.Thread(
        target=_hold_resource_lock,
        args=(config_path, entered, 0.4),
        daemon=True,
    )
    holder.start()
    assert await asyncio.to_thread(entered.wait, 1.0)

    command = asyncio.create_task(
        service.auth_command(
            CommandIntent(
                name=CommandName.AUTH,
                arguments=("deepseek", "environment"),
            )
        )
    )
    try:
        await asyncio.sleep(0.05)
        command.cancel("primary-cancellation")
        await asyncio.sleep(0.05)
        assert not command.done()
        command.cancel("later-cancellation")
        with pytest.raises(asyncio.CancelledError) as captured:
            await command
    finally:
        await asyncio.to_thread(holder.join, 1.0)

    assert captured.value.args == ("primary-cancellation",)
    assert (
        sources().provider_credentials.deepseek.selected_source
        is CredentialSource.ENVIRONMENT
    )
    assert (
        runtime[0][0].provider_credentials.deepseek.selected_source
        is CredentialSource.ENVIRONMENT
    )
    assert runtime[0] is not initial_runtime


@pytest.mark.asyncio
async def test_cancelled_model_change_keeps_user_default_and_thread_consistent(
    tmp_path: Path,
) -> None:
    runtime: list[tuple[LoadedConfigSources, ApplicationConfig]] = []
    service, conversation, sources = _service(
        tmp_path,
        validator=FakeValidator(CredentialValidationStatus.VALID),
        environ={"DEEPSEEK_API_KEY": "environment-secret"},
        runtime=runtime,
    )
    thread = conversation.create_thread("workspace_1")
    config_path = AwesomePaths.from_home(tmp_path / "home").config_file
    entered = threading.Event()
    holder = threading.Thread(
        target=_hold_resource_lock,
        args=(config_path, entered, 0.4),
        daemon=True,
    )
    holder.start()
    assert await asyncio.to_thread(entered.wait, 1.0)

    operation = asyncio.create_task(
        service.model_command(
            CommandIntent(
                name=CommandName.MODEL,
                arguments=("deepseek", "deepseek/deepseek-v4-pro"),
            ),
            thread_id=thread.id,
        )
    )
    try:
        await asyncio.sleep(0.05)
        operation.cancel("configuration-shutdown")
        with pytest.raises(asyncio.CancelledError):
            await operation
    finally:
        await asyncio.to_thread(holder.join, 1.0)

    assert sources().user.providers.default_model == "deepseek/deepseek-v4-pro"
    assert conversation.read_thread(thread.id).thread.current_model == (
        "deepseek/deepseek-v4-pro"
    )
    assert runtime[0][1].providers.default_model == "deepseek/deepseek-v4-pro"


def _assert_model_state_unchanged(
    *,
    tmp_path: Path,
    conversation: ConversationService,
    thread_id: str,
    runtime: list[tuple[LoadedConfigSources, ApplicationConfig]],
    initial_runtime: tuple[LoadedConfigSources, ApplicationConfig],
) -> None:
    paths = AwesomePaths.from_home(tmp_path / "home")
    assert UserConfigWriter(paths.config_file).read().providers.default_model is None
    assert conversation.read_thread(thread_id).thread.current_model == (
        "deepseek/deepseek-v4-flash"
    )
    assert runtime[0] is initial_runtime


@pytest.mark.asyncio
async def test_model_thread_not_found_after_config_write_rolls_back_every_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime: list[tuple[LoadedConfigSources, ApplicationConfig]] = []
    service, conversation, _ = _service(
        tmp_path,
        validator=FakeValidator(CredentialValidationStatus.VALID),
        environ={"DEEPSEEK_API_KEY": "environment-secret"},
        runtime=runtime,
    )
    thread = conversation.create_thread(
        "workspace_1",
        current_model="deepseek/deepseek-v4-flash",
    )
    initial_runtime = runtime[0]
    primary = ThreadNotFound(thread.id)

    def fail_model_update(thread_id: str, model: str | None) -> object:
        del thread_id, model
        raise primary

    monkeypatch.setattr(conversation, "set_model", fail_model_update)

    with pytest.raises(ThreadNotFound) as captured:
        await service.model_command(
            CommandIntent(
                name=CommandName.MODEL,
                arguments=("deepseek", "deepseek/deepseek-v4-pro"),
            ),
            thread_id=thread.id,
        )

    assert captured.value is primary
    _assert_model_state_unchanged(
        tmp_path=tmp_path,
        conversation=conversation,
        thread_id=thread.id,
        runtime=runtime,
        initial_runtime=initial_runtime,
    )


@pytest.mark.asyncio
async def test_model_sqlite_failure_after_config_write_rolls_back_every_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime: list[tuple[LoadedConfigSources, ApplicationConfig]] = []
    service, conversation, _ = _service(
        tmp_path,
        validator=FakeValidator(CredentialValidationStatus.VALID),
        environ={"DEEPSEEK_API_KEY": "environment-secret"},
        runtime=runtime,
    )
    thread = conversation.create_thread(
        "workspace_1",
        current_model="deepseek/deepseek-v4-flash",
    )
    initial_runtime = runtime[0]
    primary = sqlite3.OperationalError("injected SQLite write failure")

    def fail_model_update(thread_id: str, model: str | None) -> object:
        del thread_id, model
        raise primary

    monkeypatch.setattr(conversation, "set_model", fail_model_update)

    with pytest.raises(sqlite3.OperationalError) as captured:
        await service.model_command(
            CommandIntent(
                name=CommandName.MODEL,
                arguments=("deepseek", "deepseek/deepseek-v4-pro"),
            ),
            thread_id=thread.id,
        )

    assert captured.value is primary
    _assert_model_state_unchanged(
        tmp_path=tmp_path,
        conversation=conversation,
        thread_id=thread.id,
        runtime=runtime,
        initial_runtime=initial_runtime,
    )


@pytest.mark.asyncio
async def test_model_post_commit_exception_restores_thread_and_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime: list[tuple[LoadedConfigSources, ApplicationConfig]] = []
    service, conversation, _ = _service(
        tmp_path,
        validator=FakeValidator(CredentialValidationStatus.VALID),
        environ={"DEEPSEEK_API_KEY": "environment-secret"},
        runtime=runtime,
    )
    thread = conversation.create_thread(
        "workspace_1",
        current_model="deepseek/deepseek-v4-flash",
    )
    initial_runtime = runtime[0]
    primary = sqlite3.OperationalError("injected post-commit failure")
    set_model = conversation.set_model
    raised = False

    def commit_then_fail(thread_id: str, model: str | None) -> object:
        nonlocal raised
        updated = set_model(thread_id, model)
        if model == "deepseek/deepseek-v4-pro" and not raised:
            raised = True
            raise primary
        return updated

    monkeypatch.setattr(conversation, "set_model", commit_then_fail)

    with pytest.raises(sqlite3.OperationalError) as captured:
        await service.model_command(
            CommandIntent(
                name=CommandName.MODEL,
                arguments=("deepseek", "deepseek/deepseek-v4-pro"),
            ),
            thread_id=thread.id,
        )

    assert captured.value is primary
    _assert_model_state_unchanged(
        tmp_path=tmp_path,
        conversation=conversation,
        thread_id=thread.id,
        runtime=runtime,
        initial_runtime=initial_runtime,
    )
    assert service._model_transaction_journal.read() is None


@pytest.mark.asyncio
async def test_model_snapshot_io_failure_rolls_back_before_thread_update(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime: list[tuple[LoadedConfigSources, ApplicationConfig]] = []
    service, conversation, _ = _service(
        tmp_path,
        validator=FakeValidator(CredentialValidationStatus.VALID),
        environ={"DEEPSEEK_API_KEY": "environment-secret"},
        runtime=runtime,
    )
    thread = conversation.create_thread(
        "workspace_1",
        current_model="deepseek/deepseek-v4-flash",
    )
    initial_runtime = runtime[0]
    primary = OSError("injected configuration reload failure")
    model_updates = 0

    def fail_load() -> tuple[LoadedConfigSources, ApplicationConfig]:
        raise primary

    def observe_model_update(thread_id: str, model: str | None) -> object:
        nonlocal model_updates
        del thread_id, model
        model_updates += 1
        raise AssertionError("Thread update must follow a successful reload.")

    monkeypatch.setattr(service, "_load_configuration", fail_load)
    monkeypatch.setattr(conversation, "set_model", observe_model_update)

    with pytest.raises(OSError) as captured:
        await service.model_command(
            CommandIntent(
                name=CommandName.MODEL,
                arguments=("deepseek", "deepseek/deepseek-v4-pro"),
            ),
            thread_id=thread.id,
        )

    assert captured.value is primary
    assert model_updates == 0
    _assert_model_state_unchanged(
        tmp_path=tmp_path,
        conversation=conversation,
        thread_id=thread.id,
        runtime=runtime,
        initial_runtime=initial_runtime,
    )


@pytest.mark.asyncio
async def test_model_compensation_failure_requires_restart_and_preserves_primary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    runtime: list[tuple[LoadedConfigSources, ApplicationConfig]] = []
    service, conversation, _ = _service(
        tmp_path,
        validator=FakeValidator(CredentialValidationStatus.VALID),
        environ={"DEEPSEEK_API_KEY": "environment-secret"},
        runtime=runtime,
    )
    thread = conversation.create_thread(
        "workspace_1",
        current_model="deepseek/deepseek-v4-flash",
    )
    initial_runtime = runtime[0]
    primary = sqlite3.OperationalError("injected SQLite write failure")
    rollback = OSError("injected rollback failure")

    def fail_model_update(thread_id: str, model: str | None) -> object:
        del thread_id, model
        raise primary

    def fail_config_restore(document: object) -> object:
        del document
        raise rollback

    monkeypatch.setattr(conversation, "set_model", fail_model_update)
    monkeypatch.setattr(service._config_writer, "replace", fail_config_restore)

    with pytest.raises(ProviderConfigurationRecoveryRequired) as captured:
        await service.model_command(
            CommandIntent(
                name=CommandName.MODEL,
                arguments=("deepseek", "deepseek/deepseek-v4-pro"),
            ),
            thread_id=thread.id,
        )

    failure = captured.value
    assert failure.primary_error is primary
    assert failure.__cause__ is primary
    assert failure.recovery_failures[0] == ("config_restore", rollback)
    assert "config_verify" in {stage for stage, _ in failure.recovery_failures}
    assert str(failure).endswith("Restart Awesome before continuing.")
    assert "Provider configuration recovery requires restart" in caplog.text
    assert runtime[0] is initial_runtime
    assert conversation.read_thread(thread.id).thread.current_model == (
        "deepseek/deepseek-v4-flash"
    )
    assert service._model_transaction_journal.read() is not None
    blocked = await service.model_command(
        CommandIntent(
            name=CommandName.MODEL,
            arguments=("deepseek", "deepseek/deepseek-v4-pro"),
        ),
        thread_id=thread.id,
    )
    assert isinstance(blocked, CommandError)
    assert blocked.code == "recovery_required"


@pytest.mark.asyncio
async def test_cancelled_model_recovery_failure_is_persisted_and_fenced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    runtime: list[tuple[LoadedConfigSources, ApplicationConfig]] = []
    service, conversation, _ = _service(
        tmp_path,
        validator=FakeValidator(CredentialValidationStatus.VALID),
        environ={"DEEPSEEK_API_KEY": "environment-secret"},
        runtime=runtime,
    )
    thread = conversation.create_thread(
        "workspace_1",
        current_model="deepseek/deepseek-v4-flash",
    )
    initial_runtime = runtime[0]
    primary = sqlite3.OperationalError("injected SQLite write failure")
    rollback = OSError("injected rollback failure")

    def fail_model_update(thread_id: str, model: str | None) -> object:
        del thread_id, model
        raise primary

    def fail_config_restore(document: object) -> object:
        del document
        raise rollback

    monkeypatch.setattr(conversation, "set_model", fail_model_update)
    monkeypatch.setattr(service._config_writer, "replace", fail_config_restore)
    config_path = AwesomePaths.from_home(tmp_path / "home").config_file
    entered = threading.Event()
    holder = threading.Thread(
        target=_hold_resource_lock,
        args=(config_path, entered, 0.4),
        daemon=True,
    )
    holder.start()
    assert await asyncio.to_thread(entered.wait, 1.0)

    operation = asyncio.create_task(
        service.model_command(
            CommandIntent(
                name=CommandName.MODEL,
                arguments=("deepseek", "deepseek/deepseek-v4-pro"),
            ),
            thread_id=thread.id,
        )
    )
    try:
        await asyncio.sleep(0.05)
        operation.cancel("configuration-shutdown")
        with pytest.raises(asyncio.CancelledError) as captured:
            await operation
    finally:
        await asyncio.to_thread(holder.join, 1.0)

    assert captured.value.args == ("configuration-shutdown",)
    with pytest.raises(ProviderConfigurationRecoveryRequired) as fenced:
        service.require_consistent()
    assert fenced.value.primary_error is primary
    assert service._model_transaction_journal.read() is not None
    assert runtime[0] is initial_runtime
    assert "Provider configuration recovery requires restart" in caplog.text


@pytest.mark.asyncio
async def test_cancelled_credential_save_completes_secret_and_source_transaction(
    tmp_path: Path,
) -> None:
    runtime: list[tuple[LoadedConfigSources, ApplicationConfig]] = []
    service, _, sources = _service(
        tmp_path,
        validator=FakeValidator(CredentialValidationStatus.VALID),
        runtime=runtime,
    )
    config_path = AwesomePaths.from_home(tmp_path / "home").config_file
    entered = threading.Event()
    holder = threading.Thread(
        target=_hold_resource_lock,
        args=(config_path, entered, 0.4),
        daemon=True,
    )
    holder.start()
    assert await asyncio.to_thread(entered.wait, 1.0)

    operation = asyncio.create_task(
        service.set_credential(
            ProviderCredentialSetRequest(
                provider="mem0",
                action="add",
                api_key=SecretStr("mem0-secret"),
            )
        )
    )
    try:
        await asyncio.sleep(0.05)
        operation.cancel("configuration-shutdown")
        with pytest.raises(asyncio.CancelledError):
            await operation
    finally:
        await asyncio.to_thread(holder.join, 1.0)

    assert dotenv_values(tmp_path / "home" / ".env")["MEM0_API_KEY"] == ("mem0-secret")
    assert (
        sources().provider_credentials.mem0.selected_source is CredentialSource.AWESOME
    )
    assert (
        runtime[0][0].provider_credentials.mem0.selected_source
        is CredentialSource.AWESOME
    )


@pytest.mark.asyncio
async def test_environment_only_keeps_awesome_available_for_configuration(
    tmp_path: Path,
) -> None:
    service, _, _ = _service(
        tmp_path,
        validator=FakeValidator(CredentialValidationStatus.VALID),
        environ={"DEEPSEEK_API_KEY": "environment-secret"},
    )

    picker = await service.auth_command(
        CommandIntent(name=CommandName.AUTH, arguments=("deepseek",))
    )
    prompt = await service.auth_command(
        CommandIntent(name=CommandName.AUTH, arguments=("deepseek", "awesome"))
    )

    assert isinstance(picker, CommandInteractionResult)
    assert picker.interaction.kind == "selection"
    assert [
        (option.value, option.selected, option.disabled)
        for option in picker.interaction.options
    ] == [
        ("environment", True, False),
        ("awesome", False, False),
    ]
    assert isinstance(prompt, CommandInteractionResult)
    assert prompt.interaction.kind == "secret"


@pytest.mark.asyncio
async def test_deleting_selected_awesome_key_never_falls_back_to_environment(
    tmp_path: Path,
) -> None:
    service, _, sources = _service(
        tmp_path,
        validator=FakeValidator(CredentialValidationStatus.VALID),
        environ={"DEEPSEEK_API_KEY": "environment-secret"},
    )
    await service.set_credential(
        ProviderCredentialSetRequest(
            provider="deepseek",
            action="add",
            api_key=SecretStr("awesome-secret"),
        )
    )

    deleted = await service.set_credential(
        ProviderCredentialSetRequest(provider="deepseek", action="delete")
    )
    status = sources().provider_credentials.deepseek
    top = await service.auth_command(CommandIntent(name=CommandName.AUTH))

    assert deleted.status is ProviderCredentialSetStatus.DELETED
    assert deleted.source is CredentialSource.AWESOME
    assert status.selected_source is CredentialSource.AWESOME
    assert status.source_available is False
    assert status.environment_configured is True
    assert isinstance(top, CommandInteractionResult)
    assert top.interaction.kind == "selection"
    assert top.interaction.options[0].description == ("Active · awesome · Unavailable")


@pytest.mark.asyncio
async def test_selected_unavailable_source_fails_without_fallback(
    tmp_path: Path,
) -> None:
    service, _, sources = _service(
        tmp_path,
        validator=FakeValidator(CredentialValidationStatus.VALID),
    )
    service._config_writer.update(
        lambda current: current.model_copy(
            update={
                "credentials": current.credentials.model_copy(
                    update={"deepseek": CredentialSource.ENVIRONMENT}
                )
            }
        )
    )
    UserSecretStore(tmp_path / "home" / ".env").set(
        "DEEPSEEK_API_KEY", SecretStr("awesome-secret")
    )

    status = sources().provider_credentials.deepseek
    result = await service.auth_command(
        CommandIntent(
            name=CommandName.AUTH,
            arguments=("deepseek", "environment"),
        )
    )

    assert status.selected_source is CredentialSource.ENVIRONMENT
    assert status.configured is False
    assert isinstance(result, CommandError)
    assert result.code == "selected_credential_unavailable"
    assert sources().secrets.deepseek_api_key is None


@pytest.mark.asyncio
async def test_mem0_uses_the_same_masked_awesome_credential_flow(
    tmp_path: Path,
) -> None:
    service, _, sources = _service(
        tmp_path,
        validator=FakeValidator(CredentialValidationStatus.VALID),
    )
    prompt = await service.auth_command(
        CommandIntent(name=CommandName.AUTH, arguments=("mem0", "awesome"))
    )
    saved = await service.set_credential(
        ProviderCredentialSetRequest(
            provider="mem0", action="add", api_key=SecretStr("mem0-secret")
        )
    )

    assert isinstance(prompt, CommandInteractionResult)
    assert prompt.interaction.kind == "secret"
    assert prompt.interaction.provider == "mem0"
    assert saved.source is CredentialSource.AWESOME
    assert sources().provider_credentials.mem0.configured is True


@pytest.mark.asyncio
async def test_delete_removes_only_the_selected_user_credential(
    tmp_path: Path,
) -> None:
    service, _, sources = _service(
        tmp_path,
        validator=FakeValidator(CredentialValidationStatus.VALID),
    )
    store = UserSecretStore(tmp_path / "home" / ".env")
    store.set("DEEPSEEK_API_KEY", SecretStr("deepseek-secret"))
    store.set("MOONSHOT_API_KEY", SecretStr("kimi-secret"))

    result = await service.set_credential(
        ProviderCredentialSetRequest(provider="deepseek", action="delete")
    )

    values = dotenv_values(tmp_path / "home" / ".env")
    assert result.status is ProviderCredentialSetStatus.DELETED
    assert result.source in {CredentialSource.AWESOME, None}
    assert "DEEPSEEK_API_KEY" not in values
    assert values["MOONSHOT_API_KEY"] == "kimi-secret"
    assert sources().provider_credentials.deepseek.configured is False
    assert sources().provider_credentials.kimi.configured is True


def test_credential_result_contract_cannot_contain_secret_content() -> None:
    assert "api_key" not in ProviderCredentialSetResult.model_fields


@pytest.mark.asyncio
async def test_model_selection_updates_current_thread_and_user_default_only(
    tmp_path: Path,
) -> None:
    service, conversation, sources = _service(
        tmp_path,
        validator=FakeValidator(CredentialValidationStatus.VALID),
    )
    UserSecretStore(tmp_path / "home" / ".env").set(
        "MOONSHOT_API_KEY",
        SecretStr("secret"),
    )
    current = conversation.create_thread("workspace_1")
    other = conversation.create_thread(
        "workspace_1",
        current_model="deepseek/deepseek-v4-flash",
    )

    result = await service.model_command(
        CommandIntent(
            name=CommandName.MODEL,
            arguments=("kimi", "kimi/kimi-k2.6"),
        ),
        thread_id=current.id,
    )

    assert isinstance(result, CommandResult)
    assert result.payload == ModelCommandPayload(
        model="kimi/kimi-k2.6",
        default_model_updated=True,
    )
    assert conversation.read_thread(current.id).thread.current_model == (
        "kimi/kimi-k2.6"
    )
    assert conversation.read_thread(other.id).thread.current_model == (
        "deepseek/deepseek-v4-flash"
    )
    assert sources().user.providers.default_model == "kimi/kimi-k2.6"


@pytest.mark.asyncio
async def test_doctor_validates_configured_providers_only_on_demand(
    tmp_path: Path,
) -> None:
    validator = FakeValidator(CredentialValidationStatus.VALID)
    service, _, _ = _service(tmp_path, validator=validator)
    UserSecretStore(tmp_path / "home" / ".env").set(
        "DEEPSEEK_API_KEY",
        SecretStr("doctor-secret"),
    )

    assert validator.calls == []
    results = await service.doctor()

    assert results == {"deepseek": "valid", "kimi": "missing"}
    assert validator.calls == [("deepseek", "doctor-secret")]
