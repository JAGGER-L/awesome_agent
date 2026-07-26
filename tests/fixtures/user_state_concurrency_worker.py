from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

from pydantic import SecretStr

from awesome_agent.application.command_results import CommandResult, ModelCommandPayload
from awesome_agent.application.commands import CommandIntent, CommandName
from awesome_agent.application.contracts import ProviderCredentialSetRequest
from awesome_agent.application.provider_configuration import (
    ProviderConfigurationService,
    reconcile_provider_credential_transaction,
    reconcile_provider_model_transaction,
)
from awesome_agent.config import (
    ApplicationConfig,
    CredentialSource,
    CredentialValidation,
    KimiRegion,
    LoadedConfigSources,
    ProviderCredentialTransactionJournal,
    ProviderCredentialTransactionRecord,
    ProviderName,
    SecretFileSnapshot,
    UserConfigDocument,
    UserConfigWriter,
    UserSecretStore,
    load_config_sources,
    resolve_application_config,
)
from awesome_agent.config.model_transaction import (
    ProviderModelTransactionJournal,
    ProviderModelTransactionRecord,
)
from awesome_agent.conversation import ConversationService
from awesome_agent.conversation.models import Thread, ThreadView
from awesome_agent.memory.local_file import LocalMemoryFile
from awesome_agent.memory.models import MemoryScope
from awesome_agent.paths import AwesomePaths
from awesome_agent.storage.conversations import SQLiteConversationRepositories


def _wait_for(path: Path, *, timeout_seconds: float = 10.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while not path.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Timed out waiting for {path.name}.")
        time.sleep(0.01)


def _race_point(marker: Path, peer_marker: Path) -> None:
    marker.write_text("ready", encoding="utf-8")
    deadline = time.monotonic() + 0.75
    while not peer_marker.exists() and time.monotonic() < deadline:
        time.sleep(0.005)


def _update_config(
    path: Path,
    action: str,
    marker: Path,
    peer_marker: Path,
) -> None:
    def transform(current: UserConfigDocument) -> UserConfigDocument:
        _race_point(marker, peer_marker)
        if action == "model":
            return current.model_copy(
                update={
                    "providers": current.providers.model_copy(
                        update={"default_model": "deepseek/deepseek-v4-pro"}
                    )
                }
            )
        if action == "memory":
            return current.model_copy(
                update={
                    "memory": current.memory.model_copy(
                        update={"local_file_memory": True}
                    )
                }
            )
        raise ValueError(f"Unsupported config action: {action}")

    UserConfigWriter(path).update(transform)


def _update_credentials(
    path: Path,
    action: str,
    marker: Path,
    peer_marker: Path,
) -> None:
    import awesome_agent.config.credentials as credentials

    read_snapshot = credentials._read_snapshot

    def synchronized_read(target: Path) -> SecretFileSnapshot:
        snapshot = read_snapshot(target)
        _race_point(marker, peer_marker)
        return snapshot

    with patch.object(credentials, "_read_snapshot", synchronized_read):
        UserSecretStore(path).set(action, SecretStr(f"secret-{action.lower()}"))


def _update_memory(
    path: Path,
    action: str,
    marker: Path,
    peer_marker: Path,
) -> str:
    import awesome_agent.memory.local_file as local_file

    atomic_replace = local_file._atomic_replace

    def synchronized_replace(target: Path, raw: bytes) -> None:
        _race_point(marker, peer_marker)
        atomic_replace(target, raw)

    memory = LocalMemoryFile(
        path=path,
        scope=MemoryScope.USER,
        id_factory=lambda: f"memory_{action * 32}",
    )
    observed = memory.snapshot()
    with patch.object(local_file, "_atomic_replace", synchronized_replace):
        return memory.add(
            f"fact-{action}",
            expected_hash=observed.content_hash,
        ).status.value


class _BlockingProviderSecretStore(UserSecretStore):
    def __init__(self, path: Path, *, marker: Path, release: Path) -> None:
        super().__init__(path)
        self._marker = marker
        self._release = release

    def set(self, name: str, value: SecretStr) -> None:
        super().set(name, value)
        self._marker.write_text("secret-written", encoding="utf-8")
        _wait_for(self._release)


class _PhaseBlockingCredentialSecretStore(UserSecretStore):
    def __init__(
        self,
        path: Path,
        *,
        crash_phase: str,
        phase_reached: Path,
        release: Path,
    ) -> None:
        super().__init__(path)
        self._crash_phase = crash_phase
        self._phase_reached = phase_reached
        self._release = release

    def set(self, name: str, value: SecretStr) -> None:
        super().set(name, value)
        if self._crash_phase == "secret_write":
            self._phase_reached.write_text("secret_write", encoding="utf-8")
            _wait_for(self._release, timeout_seconds=60.0)


class _PhaseBlockingCredentialJournal(ProviderCredentialTransactionJournal):
    def __init__(
        self,
        journal_path: Path,
        backup_path: Path,
        *,
        crash_phase: str,
        phase_reached: Path,
        release: Path,
    ) -> None:
        super().__init__(journal_path, backup_path)
        self._crash_phase = crash_phase
        self._phase_reached = phase_reached
        self._release = release

    def _block(self, phase: str) -> None:
        if self._crash_phase != phase:
            return
        self._phase_reached.write_text(phase, encoding="utf-8")
        _wait_for(self._release, timeout_seconds=60.0)

    def prepare(
        self,
        record: ProviderCredentialTransactionRecord,
    ) -> ProviderCredentialTransactionRecord:
        prepared = super().prepare(record)
        self._block("prepared")
        return prepared

    def mark_secret_committed(
        self,
        prepared: ProviderCredentialTransactionRecord,
    ) -> ProviderCredentialTransactionRecord:
        committed = super().mark_secret_committed(prepared)
        self._block("secret_committed")
        return committed

    def mark_committed(
        self,
        secret_committed: ProviderCredentialTransactionRecord,
    ) -> ProviderCredentialTransactionRecord:
        committed = super().mark_committed(secret_committed)
        self._block("committed")
        return committed


class _PhaseBlockingCredentialConfigWriter(UserConfigWriter):
    def __init__(
        self,
        path: Path,
        *,
        crash_phase: str,
        phase_reached: Path,
        release: Path,
    ) -> None:
        super().__init__(path)
        self._crash_phase = crash_phase
        self._phase_reached = phase_reached
        self._release = release

    def update(
        self,
        transform: Callable[[UserConfigDocument], UserConfigDocument],
    ) -> UserConfigDocument:
        updated = super().update(transform)
        if self._crash_phase == "config":
            self._phase_reached.write_text("config", encoding="utf-8")
            _wait_for(self._release, timeout_seconds=60.0)
        return updated


class _ObservingProviderSecretStore(UserSecretStore):
    def __init__(self, path: Path, *, marker: Path) -> None:
        super().__init__(path)
        self._marker = marker

    def delete(self, name: str) -> bool:
        self._marker.write_text("delete-entered", encoding="utf-8")
        return super().delete(name)


class _UnexpectedCredentialValidator:
    async def validate(
        self,
        provider: ProviderName,
        api_key: SecretStr,
        *,
        kimi_region: KimiRegion,
    ) -> CredentialValidation:
        del provider, api_key, kimi_region
        raise AssertionError(
            "Mem0 credential writes must not call provider validation."
        )


class _ObservingUserConfigWriter(UserConfigWriter):
    def __init__(self, path: Path, *, update_entered: Path) -> None:
        super().__init__(path)
        self._update_entered = update_entered

    def update(
        self,
        transform: Callable[[UserConfigDocument], UserConfigDocument],
    ) -> UserConfigDocument:
        def observed(current: UserConfigDocument) -> UserConfigDocument:
            self._update_entered.write_text("update-entered", encoding="utf-8")
            return transform(current)

        return super().update(observed)


class _BarrierConversationRepositories(SQLiteConversationRepositories):
    def __init__(
        self,
        path: Path,
        *,
        marker: Path,
        peer_marker: Path,
        writes_last: bool,
    ) -> None:
        super().__init__(path)
        self._marker = marker
        self._peer_marker = peer_marker
        self._writes_last = writes_last
        self._synchronized = False

    def read_thread(self, thread_id: str) -> ThreadView:
        view = super().read_thread(thread_id)
        self._synchronize()
        return view

    def _patch_thread(
        self,
        thread_id: str,
        update: dict[str, object],
    ) -> Thread:
        self._synchronize()
        return super()._patch_thread(thread_id, update)

    def _synchronize(self) -> None:
        if not self._synchronized:
            self._synchronized = True
            self._marker.write_text("thread-read", encoding="utf-8")
            _wait_for(self._peer_marker)
            if self._writes_last:
                time.sleep(0.2)


class _PhaseBlockingJournal(ProviderModelTransactionJournal):
    def __init__(
        self,
        path: Path,
        *,
        crash_phase: str,
        phase_reached: Path,
        release: Path,
    ) -> None:
        super().__init__(path)
        self._crash_phase = crash_phase
        self._phase_reached = phase_reached
        self._release = release

    def prepare(
        self,
        record: ProviderModelTransactionRecord,
    ) -> ProviderModelTransactionRecord:
        prepared = super().prepare(record)
        self._block_at("prepared")
        return prepared

    def mark_committed(
        self,
        prepared: ProviderModelTransactionRecord,
    ) -> ProviderModelTransactionRecord:
        committed = super().mark_committed(prepared)
        self._block_at("committed")
        return committed

    def _block_at(self, phase: str) -> None:
        if self._crash_phase != phase:
            return
        self._phase_reached.write_text(phase, encoding="utf-8")
        _wait_for(self._release, timeout_seconds=60.0)


class _PhaseBlockingConfigWriter(UserConfigWriter):
    def __init__(
        self,
        path: Path,
        *,
        crash_phase: str,
        phase_reached: Path,
        release: Path,
    ) -> None:
        super().__init__(path)
        self._crash_phase = crash_phase
        self._phase_reached = phase_reached
        self._release = release

    def update(
        self,
        transform: Callable[[UserConfigDocument], UserConfigDocument],
    ) -> UserConfigDocument:
        updated = super().update(transform)
        if self._crash_phase == "config":
            self._phase_reached.write_text("config", encoding="utf-8")
            _wait_for(self._release, timeout_seconds=60.0)
        return updated


class _PhaseBlockingConversationService(ConversationService):
    def __init__(
        self,
        *,
        store: SQLiteConversationRepositories,
        crash_phase: str,
        phase_reached: Path,
        release: Path,
    ) -> None:
        super().__init__(store=store)
        self._crash_phase = crash_phase
        self._phase_reached = phase_reached
        self._release = release

    def set_model(self, thread_id: str, model: str | None) -> Thread:
        updated = super().set_model(thread_id, model)
        if self._crash_phase == "thread":
            self._phase_reached.write_text("thread", encoding="utf-8")
            _wait_for(self._release, timeout_seconds=60.0)
        return updated


def _provider_transaction(
    config_path: Path,
    action: str,
    marker: Path,
    coordination_marker: Path,
) -> None:
    paths = AwesomePaths.from_home(config_path.parent)
    workspace = config_path.parent / "workspace"

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

    if action == "add":
        secret_store: UserSecretStore = _BlockingProviderSecretStore(
            paths.env_file,
            marker=marker,
            release=coordination_marker,
        )
        request = ProviderCredentialSetRequest(
            provider="mem0",
            action="add",
            api_key=SecretStr("mem0-secret"),
        )
    elif action == "delete":
        secret_store = _ObservingProviderSecretStore(
            paths.env_file,
            marker=marker,
        )
        request = ProviderCredentialSetRequest(provider="mem0", action="delete")
    else:
        raise ValueError(f"Unsupported provider transaction action: {action}")

    service = ProviderConfigurationService(
        conversation=ConversationService(
            store=SQLiteConversationRepositories(
                config_path.parent / f"provider-{action}.db"
            )
        ),
        config_writer=UserConfigWriter(paths.config_file),
        secret_store=secret_store,
        validator=_UnexpectedCredentialValidator(),
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
    if action == "delete":
        coordination_marker.write_text("delete-attempting", encoding="utf-8")
    asyncio.run(service.set_credential(request))


def _provider_configuration_race(
    config_path: Path,
    kind: str,
    action: str,
    marker: Path,
    release: Path,
) -> dict[str, str]:
    role, value = action.split(":", maxsplit=1)
    if role not in {"first", "second"}:
        raise ValueError(f"Unsupported provider race role: {role}")

    paths = AwesomePaths.from_home(config_path.parent)
    workspace = config_path.parent / "workspace"
    application_db = config_path.parent / "application.db"
    thread_id = (config_path.parent / "thread-id").read_text(encoding="utf-8")
    environment = {"DEEPSEEK_API_KEY": "environment-secret"}
    applied: list[tuple[LoadedConfigSources, ApplicationConfig]] = []

    def sources() -> LoadedConfigSources:
        return load_config_sources(
            paths=paths,
            workspace=workspace,
            workspace_trusted=True,
            environ=environment,
        )

    def load_configuration() -> tuple[LoadedConfigSources, ApplicationConfig]:
        if role == "first":
            marker.write_text("load-entered", encoding="utf-8")
            _wait_for(release)
        loaded = sources()
        return loaded, resolve_application_config(loaded)

    def apply_configuration(
        snapshot: tuple[LoadedConfigSources, ApplicationConfig],
    ) -> None:
        applied.append(snapshot)

    writer: UserConfigWriter
    if role == "second":
        writer = _ObservingUserConfigWriter(
            paths.config_file,
            update_entered=marker,
        )
    else:
        writer = UserConfigWriter(paths.config_file)
    conversation = ConversationService(
        store=SQLiteConversationRepositories(application_db)
    )
    service = ProviderConfigurationService(
        conversation=conversation,
        config_writer=writer,
        secret_store=UserSecretStore(paths.env_file),
        validator=_UnexpectedCredentialValidator(),
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

    if kind == "provider_model":
        provider = "deepseek" if value.startswith("deepseek/") else "kimi"
        outcome = asyncio.run(
            service.model_command(
                CommandIntent(
                    name=CommandName.MODEL,
                    arguments=(provider, value),
                ),
                thread_id=thread_id,
            )
        )
        assert isinstance(outcome, CommandResult)
        assert isinstance(outcome.payload, ModelCommandPayload)
        assert len(applied) == 1
        return {
            "status": "completed",
            "snapshot_model": applied[0][1].providers.default_model or "",
            "result_model": outcome.payload.model,
            "thread_model": (
                conversation.read_thread(thread_id).thread.current_model or ""
            ),
        }

    if kind == "provider_source":
        source = CredentialSource(value)
        arguments = (
            ("deepseek", source.value, "use")
            if source is CredentialSource.AWESOME
            else ("deepseek", source.value)
        )
        outcome = asyncio.run(
            service.auth_command(
                CommandIntent(
                    name=CommandName.AUTH,
                    arguments=arguments,
                )
            )
        )
        assert isinstance(outcome, CommandResult)
        assert len(applied) == 1
        selected = applied[0][0].provider_credentials.deepseek.selected_source
        return {
            "status": "completed",
            "snapshot_source": selected.value if selected is not None else "",
        }

    raise ValueError(f"Unsupported provider race kind: {kind}")


def _thread_mutation_race(
    database_path: Path,
    action: str,
    marker: Path,
    peer_marker: Path,
) -> None:
    order, mutation, value = action.split(":", maxsplit=2)
    if order not in {"first", "last"}:
        raise ValueError(f"Unsupported Thread mutation order: {order}")
    thread_id = database_path.with_name("thread-id").read_text(encoding="utf-8")
    conversation = ConversationService(
        store=_BarrierConversationRepositories(
            database_path,
            marker=marker,
            peer_marker=peer_marker,
            writes_last=order == "last",
        )
    )
    if mutation == "model":
        conversation.set_model(thread_id, value)
    elif mutation == "rename":
        conversation.rename_thread(thread_id, value)
    elif mutation == "thinking":
        conversation.set_thinking(thread_id, value == "true")
    else:
        raise ValueError(f"Unsupported Thread mutation: {mutation}")


def _provider_model_crash(
    config_path: Path,
    phase: str,
    phase_reached: Path,
    release: Path,
) -> None:
    if phase not in {"prepared", "config", "thread", "committed"}:
        raise ValueError(f"Unsupported Provider crash phase: {phase}")
    paths = AwesomePaths.from_home(config_path.parent)
    workspace = config_path.parent / "workspace"
    thread_id = config_path.with_name("thread-id").read_text(encoding="utf-8")

    def sources() -> LoadedConfigSources:
        return load_config_sources(
            paths=paths,
            workspace=workspace,
            workspace_trusted=True,
            environ={"DEEPSEEK_API_KEY": "environment-secret"},
        )

    def load_configuration() -> tuple[LoadedConfigSources, ApplicationConfig]:
        loaded = sources()
        return loaded, resolve_application_config(loaded)

    conversation = _PhaseBlockingConversationService(
        store=SQLiteConversationRepositories(config_path.parent / "application.db"),
        crash_phase=phase,
        phase_reached=phase_reached,
        release=release,
    )
    service = ProviderConfigurationService(
        conversation=conversation,
        config_writer=_PhaseBlockingConfigWriter(
            paths.config_file,
            crash_phase=phase,
            phase_reached=phase_reached,
            release=release,
        ),
        secret_store=UserSecretStore(paths.env_file),
        validator=_UnexpectedCredentialValidator(),
        sources=sources,
        load_configuration=load_configuration,
        apply_configuration=lambda _: None,
        model_transaction_journal=_PhaseBlockingJournal(
            paths.provider_model_transaction_file,
            crash_phase=phase,
            phase_reached=phase_reached,
            release=release,
        ),
        credential_transaction_journal=ProviderCredentialTransactionJournal(
            paths.provider_credential_transaction_file,
            paths.provider_credential_backup_file,
        ),
    )
    asyncio.run(
        service.model_command(
            CommandIntent(
                name=CommandName.MODEL,
                arguments=("deepseek", "deepseek/deepseek-v4-pro"),
            ),
            thread_id=thread_id,
        )
    )


def _provider_model_reconcile(config_path: Path) -> dict[str, str]:
    paths = AwesomePaths.from_home(config_path.parent)
    conversation = ConversationService(
        store=SQLiteConversationRepositories(config_path.parent / "application.db")
    )
    reconciled = reconcile_provider_model_transaction(
        journal=ProviderModelTransactionJournal(paths.provider_model_transaction_file),
        config_writer=UserConfigWriter(paths.config_file),
        conversation=conversation,
    )
    thread_id = config_path.with_name("thread-id").read_text(encoding="utf-8")
    return {
        "status": "reconciled" if reconciled else "nothing_to_reconcile",
        "default_model": (
            UserConfigWriter(paths.config_file).read().providers.default_model or ""
        ),
        "thread_model": (
            conversation.read_thread(thread_id).thread.current_model or ""
        ),
    }


def _provider_credential_crash(
    config_path: Path,
    phase: str,
    phase_reached: Path,
    release: Path,
) -> None:
    paths = AwesomePaths.from_home(config_path.parent)
    workspace = config_path.parent / "workspace"

    def sources() -> LoadedConfigSources:
        return load_config_sources(
            paths=paths,
            workspace=workspace,
            workspace_trusted=True,
            environ={"DEEPSEEK_API_KEY": "environment-secret"},
        )

    def load_configuration() -> tuple[LoadedConfigSources, ApplicationConfig]:
        loaded = sources()
        return loaded, resolve_application_config(loaded)

    service = ProviderConfigurationService(
        conversation=ConversationService(
            store=SQLiteConversationRepositories(config_path.parent / "application.db")
        ),
        config_writer=_PhaseBlockingCredentialConfigWriter(
            paths.config_file,
            crash_phase=phase,
            phase_reached=phase_reached,
            release=release,
        ),
        secret_store=_PhaseBlockingCredentialSecretStore(
            paths.env_file,
            crash_phase=phase,
            phase_reached=phase_reached,
            release=release,
        ),
        validator=_UnexpectedCredentialValidator(),
        sources=sources,
        load_configuration=load_configuration,
        apply_configuration=lambda _: None,
        model_transaction_journal=ProviderModelTransactionJournal(
            paths.provider_model_transaction_file
        ),
        credential_transaction_journal=_PhaseBlockingCredentialJournal(
            paths.provider_credential_transaction_file,
            paths.provider_credential_backup_file,
            crash_phase=phase,
            phase_reached=phase_reached,
            release=release,
        ),
    )
    asyncio.run(
        service.set_credential(
            ProviderCredentialSetRequest(
                provider="mem0",
                action="replace",
                api_key=SecretStr("new-secret"),
            )
        )
    )


def _provider_credential_reconcile(config_path: Path) -> dict[str, str]:
    paths = AwesomePaths.from_home(config_path.parent)
    reconciled = reconcile_provider_credential_transaction(
        journal=ProviderCredentialTransactionJournal(
            paths.provider_credential_transaction_file,
            paths.provider_credential_backup_file,
        ),
        config_writer=UserConfigWriter(paths.config_file),
        secret_store=UserSecretStore(paths.env_file),
    )
    source = UserConfigWriter(paths.config_file).read().credentials.mem0
    return {
        "status": "reconciled" if reconciled else "nothing_to_reconcile",
        "source": source.value if source is not None else "",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "kind",
        choices=(
            "config",
            "credentials",
            "memory",
            "provider_transaction",
            "provider_model",
            "provider_source",
            "thread_mutation",
            "provider_model_crash",
            "provider_model_reconcile",
            "provider_credential_crash",
            "provider_credential_reconcile",
        ),
    )
    parser.add_argument("path", type=Path)
    parser.add_argument("action")
    parser.add_argument("ready", type=Path)
    parser.add_argument("start", type=Path)
    parser.add_argument("marker", type=Path)
    parser.add_argument("peer_marker", type=Path)
    parser.add_argument("result", type=Path)
    arguments = parser.parse_args()

    arguments.ready.write_text("ready", encoding="utf-8")
    _wait_for(arguments.start)
    status = "completed"
    if arguments.kind == "config":
        _update_config(
            arguments.path,
            arguments.action,
            arguments.marker,
            arguments.peer_marker,
        )
    elif arguments.kind == "credentials":
        _update_credentials(
            arguments.path,
            arguments.action,
            arguments.marker,
            arguments.peer_marker,
        )
    elif arguments.kind == "memory":
        status = _update_memory(
            arguments.path,
            arguments.action,
            arguments.marker,
            arguments.peer_marker,
        )
    elif arguments.kind == "provider_transaction":
        _provider_transaction(
            arguments.path,
            arguments.action,
            arguments.marker,
            arguments.peer_marker,
        )
    elif arguments.kind in {"provider_model", "provider_source"}:
        result = _provider_configuration_race(
            arguments.path,
            arguments.kind,
            arguments.action,
            arguments.marker,
            arguments.peer_marker,
        )
        arguments.result.write_text(json.dumps(result), encoding="utf-8")
        return
    elif arguments.kind == "thread_mutation":
        _thread_mutation_race(
            arguments.path,
            arguments.action,
            arguments.marker,
            arguments.peer_marker,
        )
    elif arguments.kind == "provider_model_crash":
        _provider_model_crash(
            arguments.path,
            arguments.action,
            arguments.marker,
            arguments.peer_marker,
        )
    elif arguments.kind == "provider_model_reconcile":
        result = _provider_model_reconcile(arguments.path)
        arguments.result.write_text(json.dumps(result), encoding="utf-8")
        return
    elif arguments.kind == "provider_credential_crash":
        _provider_credential_crash(
            arguments.path,
            arguments.action,
            arguments.marker,
            arguments.peer_marker,
        )
    else:
        result = _provider_credential_reconcile(arguments.path)
        arguments.result.write_text(json.dumps(result), encoding="utf-8")
        return
    arguments.result.write_text(json.dumps({"status": status}), encoding="utf-8")


if __name__ == "__main__":
    main()
