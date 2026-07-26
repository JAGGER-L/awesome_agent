from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal, Protocol, cast

from pydantic import SecretStr

from awesome_agent.application.command_results import (
    CommandOption,
    CommandOutcome,
    CommandSecretPrompt,
    CommandSelection,
    ModelCommandPayload,
    NoticeCommandPayload,
    error,
    interaction,
    result,
)
from awesome_agent.application.commands import CommandIntent
from awesome_agent.application.contracts import (
    ProviderCredentialSetRequest,
    ProviderCredentialSetResult,
    ProviderCredentialSetStatus,
)
from awesome_agent.config import (
    SUPPORTED_MODEL_IDS,
    ApplicationConfig,
    CredentialService,
    CredentialSource,
    CredentialValidation,
    CredentialValidationStatus,
    KimiRegion,
    LoadedConfigSources,
    ProviderCredentialStatus,
    ProviderCredentialTransactionJournal,
    ProviderCredentialTransactionPhase,
    ProviderCredentialTransactionRecord,
    ProviderName,
    UserConfigDocument,
    UserConfigWriter,
    UserSecretStore,
    provider_environment_variable,
)
from awesome_agent.config.model_transaction import (
    ProviderModelTransactionJournal,
    ProviderModelTransactionJournalError,
    ProviderModelTransactionPhase,
    ProviderModelTransactionRecord,
)
from awesome_agent.conversation import ConversationService, ThreadNotFound
from awesome_agent.core.cancellation import (
    finish_bounded_cancellation_cleanup,
    run_cancellation_safe_blocking_call,
)

_PROVIDER_LABELS: dict[ProviderName, str] = {
    "deepseek": "DeepSeek",
    "kimi": "Kimi",
}
_SERVICE_LABELS: dict[CredentialService, str] = {
    "deepseek": "DeepSeek",
    "kimi": "Kimi",
    "mem0": "Mem0 Cloud",
}
_SERVICE_HELP_URLS: dict[CredentialService, str] = {
    "deepseek": "https://platform.deepseek.com/api_keys",
    "kimi": "https://platform.moonshot.cn/console/api-keys",
    "mem0": "https://app.mem0.ai/dashboard/api-keys",
}

type ProviderConfigurationSnapshot = tuple[LoadedConfigSources, ApplicationConfig]

logger = logging.getLogger(__name__)

# Provider persistence can wait on two independently locked user-state files. Keep
# the runtime publication cleanup at the same bounded horizon as that transaction.
_RUNTIME_PUBLICATION_CLEANUP_TIMEOUT_SECONDS = 22.0


@dataclass(slots=True)
class ProviderConfigurationPublication:
    """Revocable authority for one committed runtime publication attempt."""

    generation: int
    _active: bool = True

    def revoke(self) -> None:
        self._active = False

    def require_active(self) -> None:
        if not self._active:
            raise RuntimeError("Provider runtime publication authority was revoked.")


async def _await_shielded_task(task: asyncio.Task[None]) -> None:
    await asyncio.shield(task)


def _consume_background_task_result(task: asyncio.Task[None]) -> None:
    if task.cancelled():
        return
    try:
        task.exception()
    except Exception:
        return


class ProviderConfigurationRecoveryRequired(RuntimeError):
    """A failed Provider mutation could not be restored to a verified state."""

    def __init__(
        self,
        primary_error: Exception,
        recovery_failures: tuple[tuple[str, Exception], ...],
    ) -> None:
        stages = ", ".join(stage for stage, _ in recovery_failures)
        super().__init__(
            "Provider configuration update failed and recovery could not be "
            f"verified ({stages}). Restart Awesome before continuing."
        )
        self.primary_error = primary_error
        self.recovery_failures = recovery_failures


def reconcile_provider_model_transaction(
    *,
    journal: ProviderModelTransactionJournal,
    config_writer: UserConfigWriter,
    conversation: ConversationService,
) -> bool:
    """Reconcile one crash-interrupted model transaction before activation."""

    with config_writer.transaction():
        try:
            record = journal.read()
        except Exception as error:
            raise ProviderConfigurationRecoveryRequired(
                error,
                (("journal_read", error),),
            ) from error
        if record is None:
            return False
        use_target = record.phase is ProviderModelTransactionPhase.COMMITTED
        try:
            default_model = (
                record.target_default_model
                if use_target
                else record.previous_default_model
            )
            thread_model = (
                record.target_thread_model
                if use_target
                else record.previous_thread_model
            )
            config_writer.update(
                lambda current: current.model_copy(
                    update={
                        "providers": current.providers.model_copy(
                            update={"default_model": default_model}
                        )
                    }
                )
            )
            conversation.set_model(record.thread_id, thread_model)
            _verify_model_transaction_state(
                record=record,
                config_writer=config_writer,
                conversation=conversation,
                use_target=use_target,
            )
            journal.clear(record)
        except Exception as error:
            raise ProviderConfigurationRecoveryRequired(
                error,
                (("startup_reconcile", error),),
            ) from error
        return True


def reconcile_provider_credential_transaction(
    *,
    journal: ProviderCredentialTransactionJournal,
    config_writer: UserConfigWriter,
    secret_store: UserSecretStore,
) -> bool:
    """Reconcile one crash-interrupted credential transaction before config use."""

    with config_writer.transaction(), secret_store.transaction():
        try:
            record = journal.read()
            if record is None:
                return journal.clear_orphan_backup()
            use_target = record.phase is ProviderCredentialTransactionPhase.COMMITTED
            current = secret_store.snapshot()
            if use_target:
                if not record.matches_target(current):
                    raise RuntimeError(
                        "Committed Provider credential state cannot be verified."
                    )
                source = record.target_source
            else:
                if not record.matches_previous(current):
                    if not record.matches_target(current):
                        raise RuntimeError(
                            "Pending Provider credential state differs from both "
                            "transaction endpoints."
                        )
                    secret_store.restore(journal.read_backup(record))
                source = record.previous_source
            _write_credential_source(
                config_writer,
                record.service,
                source,
            )
            _verify_credential_transaction_state(
                record=record,
                config_writer=config_writer,
                secret_store=secret_store,
                use_target=use_target,
            )
            journal.clear(record)
        except Exception as error:
            raise ProviderConfigurationRecoveryRequired(
                error,
                (("credential_reconcile", error),),
            ) from error
        return True


class CredentialValidator(Protocol):
    async def validate(
        self,
        provider: ProviderName,
        api_key: SecretStr,
        *,
        kimi_region: KimiRegion,
    ) -> CredentialValidation: ...


class ProviderConfigurationService:
    def __init__(
        self,
        *,
        conversation: ConversationService,
        config_writer: UserConfigWriter,
        secret_store: UserSecretStore,
        validator: CredentialValidator,
        sources: Callable[[], LoadedConfigSources],
        load_configuration: Callable[[], ProviderConfigurationSnapshot],
        apply_configuration: Callable[
            [ProviderConfigurationSnapshot, ProviderConfigurationPublication],
            Awaitable[None],
        ],
        model_transaction_journal: ProviderModelTransactionJournal,
        credential_transaction_journal: ProviderCredentialTransactionJournal,
    ) -> None:
        self._conversation = conversation
        self._config_writer = config_writer
        self._secret_store = secret_store
        self._validator = validator
        self._sources = sources
        self._load_configuration = load_configuration
        self._apply_configuration = apply_configuration
        self._model_transaction_journal = model_transaction_journal
        self._credential_transaction_journal = credential_transaction_journal
        self._recovery_required: ProviderConfigurationRecoveryRequired | None = None
        self._publication_generation = 0

    def require_consistent(self) -> None:
        with self._config_writer.transaction():
            self._require_consistent_locked()

    async def ensure_consistent(self) -> None:
        await run_cancellation_safe_blocking_call(self.require_consistent)

    def _require_consistent_locked(self) -> None:
        if self._recovery_required is not None:
            raise self._recovery_required
        try:
            pending = self._model_transaction_journal.read()
            self._credential_transaction_journal.require_clean()
        except Exception as error:
            failure = ProviderConfigurationRecoveryRequired(
                error,
                (("journal_read", error),),
            )
            self._recovery_required = failure
            raise failure from error
        if pending is not None:
            primary = ProviderModelTransactionJournalError(
                "A Provider model transaction requires startup recovery."
            )
            failure = ProviderConfigurationRecoveryRequired(
                primary,
                (("startup_reconcile_required", primary),),
            )
            self._recovery_required = failure
            raise failure from primary

    async def auth_command(self, intent: CommandIntent) -> CommandOutcome:
        arguments = intent.arguments
        if not arguments:
            return interaction(
                CommandSelection(
                    prompt="Authentication",
                    options=self._service_options(),
                ),
            )
        service = _service(arguments[0])
        if service is None:
            return _error("invalid_arguments", "Usage: /auth [deepseek|kimi|mem0]")
        status = _status(self._sources(), service)
        if len(arguments) == 1:
            return interaction(
                CommandSelection(
                    prompt=f"{_SERVICE_LABELS[service]} credential source",
                    options=(
                        CommandOption(
                            value="environment",
                            label="Environment",
                            description=(
                                f"Detected · {status.environment_variable}"
                                if status.environment_configured
                                else "Not detected"
                            ),
                            selected=status.selected_source
                            is CredentialSource.ENVIRONMENT,
                            disabled=not status.environment_configured,
                        ),
                        CommandOption(
                            value="awesome",
                            label="Awesome API key",
                            description=(
                                "Configured"
                                if status.awesome_configured
                                else "Not configured"
                            ),
                            selected=status.selected_source is CredentialSource.AWESOME,
                        ),
                    ),
                ),
            )
        source = arguments[1]
        if len(arguments) == 2 and source == CredentialSource.ENVIRONMENT.value:
            if not status.environment_configured:
                return _error(
                    "selected_credential_unavailable",
                    f"{status.environment_variable} is not available in this process.",
                )
            blocked = self._mutation_blocked()
            if blocked is not None:
                return blocked
            await self._select_source(service, CredentialSource.ENVIRONMENT)
            return result(
                NoticeCommandPayload(
                    message=f"{_SERVICE_LABELS[service]} now uses Environment."
                )
            )
        if source != CredentialSource.AWESOME.value:
            return _error("invalid_arguments", "Choose Environment or Awesome API key.")
        if len(arguments) == 2:
            if not status.awesome_configured:
                return self._secret_prompt(service, action="add")
            return interaction(
                CommandSelection(
                    prompt=f"{_SERVICE_LABELS[service]} Awesome API key",
                    options=(
                        CommandOption(value="use", label="Use this API key"),
                        CommandOption(value="replace", label="Replace API key"),
                        CommandOption(value="delete", label="Delete API key"),
                    ),
                ),
            )
        action = arguments[2]
        if len(arguments) == 3 and action == "use":
            blocked = self._mutation_blocked()
            if blocked is not None:
                return blocked
            await self._select_source(service, CredentialSource.AWESOME)
            return result(
                NoticeCommandPayload(
                    message=f"{_SERVICE_LABELS[service]} now uses Awesome API key."
                )
            )
        if len(arguments) == 3 and action == "replace":
            return self._secret_prompt(service, action="replace")
        if len(arguments) == 3 and action == "delete":
            return interaction(
                CommandSelection(
                    prompt=(
                        f"Delete {_SERVICE_LABELS[service]} API key? "
                        "This does not revoke it at the Provider."
                    ),
                    options=(
                        CommandOption(value="back", label="Cancel", selected=True),
                        CommandOption(value="confirm", label="Delete"),
                    ),
                ),
            )
        return _error("invalid_arguments", "Usage: /auth [service] [source] [action]")

    async def model_command(
        self,
        intent: CommandIntent,
        *,
        thread_id: str,
    ) -> CommandOutcome:
        arguments = intent.arguments
        if not arguments:
            return interaction(
                CommandSelection(
                    prompt="Select Provider",
                    options=self._provider_options(),
                ),
            )
        provider = _provider(arguments[0])
        if provider is None:
            return _error("invalid_arguments", "Usage: /model [deepseek|kimi]")
        status = _status(self._sources(), provider)
        if len(arguments) == 1:
            if not status.configured:
                return self._secret_prompt(provider, action="add")
            thread = self._conversation.read_thread(thread_id).thread
            models = sorted(
                model
                for model in SUPPORTED_MODEL_IDS
                if model.startswith(f"{provider}/")
            )
            return interaction(
                CommandSelection(
                    prompt=f"Select {_PROVIDER_LABELS[provider]} Model",
                    options=tuple(
                        CommandOption(
                            value=model,
                            label=model,
                            selected=model == thread.current_model,
                        )
                        for model in models
                    ),
                ),
            )
        if len(arguments) != 2:
            return _error("invalid_arguments", "Usage: /model [deepseek|kimi]")
        model = arguments[1]
        if model not in SUPPORTED_MODEL_IDS or not model.startswith(f"{provider}/"):
            return _error("unsupported_model", "Selected model is not supported.")
        if not status.configured:
            return _error(
                "provider_not_configured",
                f"{_PROVIDER_LABELS[provider]} is not configured.",
            )
        blocked = self._mutation_blocked()
        if blocked is not None:
            return blocked
        try:
            self._conversation.read_thread(thread_id)
        except ThreadNotFound:
            return _error("thread_not_found", "Thread was not found.")

        def persist_model() -> tuple[ProviderConfigurationSnapshot, str]:
            # YAML and Application SQLite cannot share one storage transaction.
            # The config lock is therefore the provider-configuration ordering
            # boundary: every successful model change writes the user default,
            # reloads that exact commit, and updates its Thread before a peer may
            # begin another provider configuration transaction.
            with self._config_writer.transaction():
                self._require_consistent_locked()
                previous_document = self._config_writer.read()
                previous_model = self._conversation.read_thread(
                    thread_id
                ).thread.current_model
                try:
                    prepared = self._model_transaction_journal.prepare(
                        ProviderModelTransactionRecord(
                            phase=ProviderModelTransactionPhase.PREPARED,
                            thread_id=thread_id,
                            previous_default_model=(
                                previous_document.providers.default_model
                            ),
                            target_default_model=model,
                            previous_thread_model=previous_model,
                            target_thread_model=model,
                        )
                    )
                except Exception as primary_error:
                    failure = ProviderConfigurationRecoveryRequired(
                        primary_error,
                        (("journal_prepare", primary_error),),
                    )
                    self._fence(failure)
                    raise failure from primary_error
                try:
                    self._config_writer.update(
                        lambda current: current.model_copy(
                            update={
                                "providers": current.providers.model_copy(
                                    update={"default_model": model}
                                )
                            }
                        )
                    )
                    snapshot = self._load_configuration()
                    self._conversation.set_model(thread_id, model)
                    _verify_model_transaction_state(
                        record=prepared,
                        config_writer=self._config_writer,
                        conversation=self._conversation,
                        use_target=True,
                    )
                except Exception as primary_error:
                    recovery_failures = self._restore_model_change(
                        record=prepared,
                        previous_document=previous_document,
                    )
                    if recovery_failures:
                        failure = ProviderConfigurationRecoveryRequired(
                            primary_error,
                            recovery_failures,
                        )
                        self._fence(failure)
                        raise failure from primary_error
                    raise
                try:
                    committed = self._model_transaction_journal.mark_committed(prepared)
                    self._model_transaction_journal.clear(committed)
                except Exception as primary_error:
                    failure = ProviderConfigurationRecoveryRequired(
                        primary_error,
                        (("journal_finalize", primary_error),),
                    )
                    self._fence(failure)
                    raise failure from primary_error
                return snapshot, model

        _, updated_model = await self._persist_and_apply(
            persist_model,
            snapshot=lambda persisted: persisted[0],
        )
        return result(
            ModelCommandPayload(
                model=updated_model,
                default_model_updated=True,
            )
        )

    async def set_credential(
        self,
        request: ProviderCredentialSetRequest,
    ) -> ProviderCredentialSetResult:
        status = _status(self._sources(), request.provider)
        if request.action == "delete":
            await self._persist_and_apply(
                lambda: self._persist_credential_transaction(request, None),
                snapshot=lambda persisted: persisted,
            )
            return ProviderCredentialSetResult(
                provider=request.provider,
                status=ProviderCredentialSetStatus.DELETED,
                source=status.selected_source,
                code="credential_deleted",
            )
        api_key = request.api_key
        assert api_key is not None
        result = (
            CredentialValidation(
                status=CredentialValidationStatus.VALID, code="credential_valid"
            )
            if request.provider == "mem0"
            else await self._validator.validate(
                request.provider,
                api_key,
                kimi_region=self._sources().user.providers.kimi_region,
            )
        )
        if result.status is CredentialValidationStatus.INVALID:
            return ProviderCredentialSetResult(
                provider=request.provider,
                status=ProviderCredentialSetStatus.INVALID,
                source=status.selected_source,
                code=result.code,
            )
        if (
            result.status is CredentialValidationStatus.UNVERIFIED
            and not request.allow_unverified
        ):
            return ProviderCredentialSetResult(
                provider=request.provider,
                status=ProviderCredentialSetStatus.CONFIRM_UNVERIFIED,
                source=status.selected_source,
                code=result.code,
            )

        await self._persist_and_apply(
            lambda: self._persist_credential_transaction(request, api_key),
            snapshot=lambda persisted: persisted,
        )
        return ProviderCredentialSetResult(
            provider=request.provider,
            status=ProviderCredentialSetStatus.CONFIGURED,
            source=CredentialSource.AWESOME,
            code=(
                "credential_saved_unverified"
                if result.status is CredentialValidationStatus.UNVERIFIED
                else "credential_saved"
            ),
        )

    async def doctor(self) -> dict[str, str]:
        sources = self._sources()
        results: dict[str, str] = {}
        for provider in ("deepseek", "kimi"):
            status = _status(sources, provider)
            if not status.configured:
                results[provider] = "missing"
                continue
            secret = (
                sources.secrets.deepseek_api_key
                if provider == "deepseek"
                else sources.secrets.moonshot_api_key
            )
            if secret is None:
                results[provider] = "missing"
                continue
            validation = await self._validator.validate(
                provider,
                secret,
                kimi_region=sources.user.providers.kimi_region,
            )
            results[provider] = validation.status.value
        return results

    def _service_options(self) -> tuple[CommandOption, ...]:
        sources = self._sources()
        options: list[CommandOption] = []
        for service in ("deepseek", "kimi", "mem0"):
            status = _status(sources, service)
            active = status.selected_source.value if status.selected_source else None
            options.append(
                CommandOption(
                    value=service,
                    label=_SERVICE_LABELS[service],
                    description=(
                        "Not configured"
                        if active is None
                        else f"Active · {active}"
                        if status.source_available
                        else f"Active · {active} · Unavailable"
                    ),
                )
            )
        return tuple(options)

    def _provider_options(self) -> tuple[CommandOption, ...]:
        return tuple(
            option for option in self._service_options() if option.value != "mem0"
        )

    async def _select_source(
        self,
        service: CredentialService,
        source: CredentialSource,
    ) -> None:
        def persist_source() -> ProviderConfigurationSnapshot:
            with self._config_writer.transaction():
                self._require_consistent_locked()
                self._write_source(service, source)
                return self._load_configuration()

        await self._persist_and_apply(
            persist_source,
            snapshot=lambda persisted: persisted,
        )

    async def _persist_and_apply[ResultT](
        self,
        persist: Callable[[], ResultT],
        *,
        snapshot: Callable[[ResultT], ProviderConfigurationSnapshot],
    ) -> ResultT:
        """Persist one mutation, then publish its runtime snapshot asynchronously."""

        completed: list[ResultT] = []

        def capture(result: ResultT) -> None:
            completed.append(result)

        try:
            persisted = await run_cancellation_safe_blocking_call(
                persist,
                on_completed=capture,
                on_abandoned=self._fence_abandoned_transaction,
            )
        except asyncio.CancelledError:
            if completed:
                await self._apply_after_committed_cancellation(
                    snapshot(completed[-1])
                )
            raise
        await self._apply_committed_configuration(snapshot(persisted))
        return persisted

    async def _apply_committed_configuration(
        self,
        snapshot: ProviderConfigurationSnapshot,
    ) -> None:
        authority = self._new_publication_authority()
        publication = asyncio.create_task(
            self._apply_and_fence(snapshot, authority),
            name="provider-runtime-publication",
        )
        try:
            await asyncio.shield(publication)
        except asyncio.CancelledError:
            await self._finish_runtime_publication(publication, authority)
            raise

    async def _apply_after_committed_cancellation(
        self,
        snapshot: ProviderConfigurationSnapshot,
    ) -> None:
        authority = self._new_publication_authority()
        publication = asyncio.create_task(
            self._apply_and_fence(snapshot, authority),
            name="provider-runtime-publication-after-cancellation",
        )
        await self._finish_runtime_publication(publication, authority)

    def _new_publication_authority(self) -> ProviderConfigurationPublication:
        self._publication_generation += 1
        return ProviderConfigurationPublication(self._publication_generation)

    async def _finish_runtime_publication(
        self,
        publication: asyncio.Task[None],
        authority: ProviderConfigurationPublication,
    ) -> None:
        await finish_bounded_cancellation_cleanup(
            _await_shielded_task(publication),
            timeout_seconds=_RUNTIME_PUBLICATION_CLEANUP_TIMEOUT_SECONDS,
        )
        if publication.done():
            if publication.cancelled():
                authority.revoke()
                self._fence_runtime_publication_abandoned("cancelled")
            else:
                # Retrieve any failure. ``_apply_and_fence`` already converted and
                # fenced ordinary publication errors before surfacing them.
                publication.exception()
            return
        authority.revoke()
        self._fence_runtime_publication_abandoned("timed_out")
        publication.cancel()
        publication.add_done_callback(_consume_background_task_result)

    async def _apply_and_fence(
        self,
        snapshot: ProviderConfigurationSnapshot,
        authority: ProviderConfigurationPublication,
    ) -> None:
        try:
            await self._apply_configuration(snapshot, authority)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            failure = ProviderConfigurationRecoveryRequired(
                error,
                (("runtime_publish", error),),
            )
            self._fence(failure)
            raise failure from error

    def _fence_runtime_publication_abandoned(self, reason: str) -> None:
        primary = RuntimeError(
            f"Committed Provider configuration publication {reason}."
        )
        self._fence(
            ProviderConfigurationRecoveryRequired(
                primary,
                (("runtime_publish_abandoned", primary),),
            )
        )

    def _write_source(
        self,
        service: CredentialService,
        source: CredentialSource,
    ) -> None:
        _write_credential_source(self._config_writer, service, source)

    def _persist_credential_transaction(
        self,
        request: ProviderCredentialSetRequest,
        api_key: SecretStr | None,
    ) -> ProviderConfigurationSnapshot:
        with self._config_writer.transaction(), self._secret_store.transaction():
            self._require_consistent_locked()
            previous_document = self._config_writer.read()
            previous_source = getattr(
                previous_document.credentials,
                request.provider,
            )
            previous_env = self._secret_store.snapshot()
            environment_variable = provider_environment_variable(request.provider)
            if request.action == "delete":
                target_env, changed = self._secret_store.plan_delete(
                    environment_variable
                )
                if not changed:
                    return self._load_configuration()
                target_source = previous_source
            else:
                if api_key is None:
                    raise ValueError("Credential content is required.")
                target_env = self._secret_store.plan_set(
                    environment_variable,
                    api_key,
                )
                target_source = CredentialSource.AWESOME
            prepared = ProviderCredentialTransactionRecord(
                phase=ProviderCredentialTransactionPhase.PREPARED,
                service=request.provider,
                environment_variable=environment_variable,
                action=request.action,
                previous_source=previous_source,
                target_source=target_source,
                previous_env_existed=previous_env.existed,
                previous_env_sha256=previous_env.content_hash,
                target_env_existed=target_env.existed,
                target_env_sha256=target_env.content_hash,
            )
            reached_commit = False
            try:
                self._credential_transaction_journal.stage_backup(previous_env)
                prepared = self._credential_transaction_journal.prepare(prepared)
                if request.action == "delete":
                    if not self._secret_store.delete(environment_variable):
                        raise RuntimeError(
                            "Provider credential disappeared before deletion."
                        )
                else:
                    assert api_key is not None
                    self._secret_store.set(environment_variable, api_key)
                if self._secret_store.snapshot() != target_env:
                    raise RuntimeError("Provider credential write verification failed.")
                secret_committed = (
                    self._credential_transaction_journal.mark_secret_committed(prepared)
                )
                self._write_source(request.provider, target_source)
                snapshot = self._load_configuration()
                _verify_credential_transaction_state(
                    record=secret_committed,
                    config_writer=self._config_writer,
                    secret_store=self._secret_store,
                    use_target=True,
                )
                committed = self._credential_transaction_journal.mark_committed(
                    secret_committed
                )
                reached_commit = True
                self._credential_transaction_journal.clear(committed)
                return snapshot
            except Exception as primary_error:
                try:
                    current = self._credential_transaction_journal.read()
                    durable_commit = reached_commit or (
                        current is not None
                        and current.phase
                        is ProviderCredentialTransactionPhase.COMMITTED
                    )
                    reconcile_provider_credential_transaction(
                        journal=self._credential_transaction_journal,
                        config_writer=self._config_writer,
                        secret_store=self._secret_store,
                    )
                    if durable_commit:
                        _verify_credential_transaction_state(
                            record=prepared,
                            config_writer=self._config_writer,
                            secret_store=self._secret_store,
                            use_target=True,
                        )
                        return self._load_configuration()
                except Exception as recovery_error:
                    failure = ProviderConfigurationRecoveryRequired(
                        primary_error,
                        (("credential_recovery", recovery_error),),
                    )
                    self._fence(failure)
                    raise failure from primary_error
                raise

    def _restore_model_change(
        self,
        *,
        record: ProviderModelTransactionRecord,
        previous_document: UserConfigDocument,
    ) -> tuple[tuple[str, Exception], ...]:
        failures: list[tuple[str, Exception]] = []
        try:
            self._config_writer.replace(previous_document)
        except Exception as error:
            failures.append(("config_restore", error))

        try:
            current_model = self._conversation.read_thread(
                record.thread_id
            ).thread.current_model
            if current_model != record.previous_thread_model:
                self._conversation.set_model(
                    record.thread_id,
                    record.previous_thread_model,
                )
        except Exception as error:
            failures.append(("thread_restore", error))

        try:
            if self._config_writer.read() != previous_document:
                raise RuntimeError("User configuration rollback verification failed.")
        except Exception as error:
            failures.append(("config_verify", error))

        try:
            _verify_model_transaction_state(
                record=record,
                config_writer=self._config_writer,
                conversation=self._conversation,
                use_target=False,
            )
        except Exception as error:
            failures.append(("state_verify", error))
        if not failures:
            try:
                self._model_transaction_journal.clear(record)
            except Exception as error:
                failures.append(("journal_clear", error))
        return tuple(failures)

    def _mutation_blocked(self) -> CommandOutcome | None:
        if self._recovery_required is not None:
            return _error(
                "recovery_required",
                "Provider configuration recovery is required. Restart Awesome.",
            )
        return None

    def _fence(self, failure: ProviderConfigurationRecoveryRequired) -> None:
        if self._recovery_required is not None:
            return
        self._recovery_required = failure
        logger.critical(
            "Provider configuration recovery requires restart; stages=%s",
            ",".join(stage for stage, _ in failure.recovery_failures),
        )

    def _fence_abandoned_transaction(self) -> None:
        primary = RuntimeError("Provider transaction outlived cancellation cleanup.")
        self._fence(
            ProviderConfigurationRecoveryRequired(
                primary,
                (("cancellation_cleanup_abandoned", primary),),
            )
        )

    def _secret_prompt(
        self,
        provider: CredentialService,
        *,
        action: Literal["add", "replace"],
    ) -> CommandOutcome:
        return interaction(
            CommandSecretPrompt(
                provider=provider,
                action=action,
                label=f"{_SERVICE_LABELS[provider]} API Key",
                environment_variable=provider_environment_variable(provider),
                help_url=_SERVICE_HELP_URLS[provider],
            ),
        )


def _provider(value: str) -> ProviderName | None:
    if value == "deepseek":
        return "deepseek"
    if value == "kimi":
        return "kimi"
    return None


def _service(value: str) -> CredentialService | None:
    if value in {"deepseek", "kimi", "mem0"}:
        return cast(CredentialService, value)
    return None


def _status(
    sources: LoadedConfigSources,
    provider: CredentialService,
) -> ProviderCredentialStatus:
    if provider == "deepseek":
        return sources.provider_credentials.deepseek
    if provider == "kimi":
        return sources.provider_credentials.kimi
    return sources.provider_credentials.mem0


def _verify_model_transaction_state(
    *,
    record: ProviderModelTransactionRecord,
    config_writer: UserConfigWriter,
    conversation: ConversationService,
    use_target: bool,
) -> None:
    expected_default = (
        record.target_default_model if use_target else record.previous_default_model
    )
    expected_thread = (
        record.target_thread_model if use_target else record.previous_thread_model
    )
    if config_writer.read().providers.default_model != expected_default:
        raise RuntimeError("Provider default model transaction verification failed.")
    if (
        conversation.read_thread(record.thread_id).thread.current_model
        != expected_thread
    ):
        raise RuntimeError("Thread model transaction verification failed.")


def _write_credential_source(
    config_writer: UserConfigWriter,
    service: CredentialService,
    source: CredentialSource | None,
) -> None:
    config_writer.update(
        lambda current: current.model_copy(
            update={
                "credentials": current.credentials.model_copy(update={service: source})
            }
        )
    )


def _verify_credential_transaction_state(
    *,
    record: ProviderCredentialTransactionRecord,
    config_writer: UserConfigWriter,
    secret_store: UserSecretStore,
    use_target: bool,
) -> None:
    expected_source = record.target_source if use_target else record.previous_source
    current_source = getattr(config_writer.read().credentials, record.service)
    if current_source is not expected_source:
        raise RuntimeError("Provider credential source verification failed.")
    current_secret_file = secret_store.snapshot()
    matches = (
        record.matches_target(current_secret_file)
        if use_target
        else record.matches_previous(current_secret_file)
    )
    if not matches:
        raise RuntimeError("Provider credential file verification failed.")


def _error(code: str, content: str) -> CommandOutcome:
    return error(code, content)
