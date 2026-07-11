from __future__ import annotations

from collections.abc import Callable
from typing import Literal, Protocol, cast

from pydantic import JsonValue, SecretStr

from awesome_agent.application.commands import (
    CommandIntent,
    CommandOption,
    CommandResult,
    CommandSecretPrompt,
    CommandSelection,
    CommandStatus,
)
from awesome_agent.application.contracts import (
    ProviderCredentialSetRequest,
    ProviderCredentialSetResult,
    ProviderCredentialSetStatus,
)
from awesome_agent.config import (
    SUPPORTED_MODEL_IDS,
    CredentialSource,
    KimiRegion,
    LoadedConfigSources,
    ProviderCredentialStatus,
    ProviderName,
    UserConfigWriter,
    UserSecretStore,
    provider_environment_variable,
)
from awesome_agent.conversation import ConversationService, ThreadNotFound
from awesome_agent.providers import (
    CredentialValidation,
    CredentialValidationStatus,
)

_PROVIDER_LABELS: dict[ProviderName, str] = {
    "deepseek": "DeepSeek",
    "kimi": "Kimi",
}
_PROVIDER_HELP_URLS: dict[ProviderName, str] = {
    "deepseek": "https://platform.deepseek.com/api_keys",
    "kimi": "https://platform.moonshot.cn/console/api-keys",
}


class CredentialValidator(Protocol):
    async def validate(
        self,
        provider: ProviderName,
        api_key: SecretStr,
        *,
        kimi_region: KimiRegion,
    ) -> CredentialValidation: ...


class ProviderCredentialManagedExternally(ValueError):
    pass


class ProviderConfigurationService:
    def __init__(
        self,
        *,
        conversation: ConversationService,
        config_writer: UserConfigWriter,
        secret_store: UserSecretStore,
        validator: CredentialValidator,
        sources: Callable[[], LoadedConfigSources],
        reload_configuration: Callable[[], None],
    ) -> None:
        self._conversation = conversation
        self._config_writer = config_writer
        self._secret_store = secret_store
        self._validator = validator
        self._sources = sources
        self._reload_configuration = reload_configuration

    async def auth_command(self, intent: CommandIntent) -> CommandResult:
        arguments = intent.arguments
        if not arguments:
            return CommandResult(
                status=CommandStatus.SUCCESS,
                selection=CommandSelection(
                    prompt="Provider Authentication",
                    options=self._provider_options(),
                ),
            )
        provider = _provider(arguments[0])
        if provider is None:
            return _error("invalid_arguments", "Usage: /auth [deepseek|kimi]")
        status = _status(self._sources(), provider)
        if len(arguments) == 1:
            if status.source is CredentialSource.MISSING:
                return self._secret_prompt(provider, action="add")
            if status.source is CredentialSource.PROCESS_ENVIRONMENT:
                return CommandResult(
                    status=CommandStatus.SUCCESS,
                    content=(
                        f"{_PROVIDER_LABELS[provider]} is configured through "
                        f"{status.environment_variable} in the process environment."
                    ),
                    data={"provider": provider, "source": status.source.value},
                )
            return CommandResult(
                status=CommandStatus.SUCCESS,
                selection=CommandSelection(
                    prompt=f"{_PROVIDER_LABELS[provider]} Authentication",
                    options=(
                        CommandOption(value="replace", label="Replace API key"),
                        CommandOption(value="remove", label="Remove API key"),
                        CommandOption(value="back", label="Back"),
                    ),
                ),
            )
        action = arguments[1]
        if len(arguments) == 2 and action == "replace":
            if status.source is CredentialSource.PROCESS_ENVIRONMENT:
                return _managed_error(status.environment_variable)
            return self._secret_prompt(provider, action="replace")
        if len(arguments) == 2 and action == "back":
            return CommandResult(status=CommandStatus.SUCCESS)
        if len(arguments) == 2 and action == "remove":
            return CommandResult(
                status=CommandStatus.SUCCESS,
                selection=CommandSelection(
                    prompt=(
                        f"Remove {_PROVIDER_LABELS[provider]} API key? "
                        "This does not revoke it at the Provider."
                    ),
                    options=(
                        CommandOption(value="back", label="Cancel", selected=True),
                        CommandOption(value="confirm", label="Remove"),
                    ),
                ),
            )
        if len(arguments) == 3 and action == "remove":
            if arguments[2] == "back":
                return CommandResult(status=CommandStatus.SUCCESS)
            if arguments[2] != "confirm":
                return _error("invalid_arguments", "Invalid removal decision.")
            if status.source is CredentialSource.PROCESS_ENVIRONMENT:
                return _managed_error(status.environment_variable)
            self._secret_store.delete(status.environment_variable)
            self._reload_configuration()
            return CommandResult(
                status=CommandStatus.SUCCESS,
                content=f"Removed the local {_PROVIDER_LABELS[provider]} credential.",
                data={"provider": provider, "removed": True},
            )
        return _error("invalid_arguments", "Usage: /auth [deepseek|kimi]")

    async def model_command(
        self,
        intent: CommandIntent,
        *,
        thread_id: str,
    ) -> CommandResult:
        arguments = intent.arguments
        if not arguments:
            return CommandResult(
                status=CommandStatus.SUCCESS,
                selection=CommandSelection(
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
            return CommandResult(
                status=CommandStatus.SUCCESS,
                selection=CommandSelection(
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
        try:
            self._conversation.read_thread(thread_id)
        except ThreadNotFound:
            return _error("thread_not_found", "Thread was not found.")
        self._config_writer.update(
            lambda current: current.model_copy(
                update={
                    "providers": current.providers.model_copy(
                        update={"default_model": model}
                    )
                }
            )
        )
        self._reload_configuration()
        updated = self._conversation.set_model(thread_id, model)
        return CommandResult(
            status=CommandStatus.SUCCESS,
            content=(f"Model changed to {model}. Default for new Threads updated."),
            data={"model": updated.current_model, "default_model_updated": True},
        )

    async def set_credential(
        self,
        request: ProviderCredentialSetRequest,
    ) -> ProviderCredentialSetResult:
        status = _status(self._sources(), request.provider)
        if status.source is CredentialSource.PROCESS_ENVIRONMENT:
            raise ProviderCredentialManagedExternally(
                "Provider credential is managed by the process environment."
            )
        result = await self._validator.validate(
            request.provider,
            request.api_key,
            kimi_region=self._sources().user.providers.kimi_region,
        )
        if result.status is CredentialValidationStatus.INVALID:
            return ProviderCredentialSetResult(
                provider=request.provider,
                status=ProviderCredentialSetStatus.INVALID,
                source=status.source,
                code=result.code,
            )
        if (
            result.status is CredentialValidationStatus.UNVERIFIED
            and not request.allow_unverified
        ):
            return ProviderCredentialSetResult(
                provider=request.provider,
                status=ProviderCredentialSetStatus.CONFIRM_UNVERIFIED,
                source=status.source,
                code=result.code,
            )
        self._secret_store.set(status.environment_variable, request.api_key)
        self._reload_configuration()
        return ProviderCredentialSetResult(
            provider=request.provider,
            status=ProviderCredentialSetStatus.SAVED,
            source=CredentialSource.USER_ENV_FILE,
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

    def _provider_options(self) -> tuple[CommandOption, ...]:
        sources = self._sources()
        options: list[CommandOption] = []
        for provider in ("deepseek", "kimi"):
            status = _status(sources, provider)
            description = {
                CredentialSource.MISSING: "Not configured",
                CredentialSource.USER_ENV_FILE: "Configured · Awesome",
                CredentialSource.PROCESS_ENVIRONMENT: "Configured · Environment",
            }[status.source]
            options.append(
                CommandOption(
                    value=provider,
                    label=_PROVIDER_LABELS[provider],
                    description=description,
                )
            )
        return tuple(options)

    def _secret_prompt(
        self,
        provider: ProviderName,
        *,
        action: Literal["add", "replace"],
    ) -> CommandResult:
        return CommandResult(
            status=CommandStatus.SUCCESS,
            secret_prompt=CommandSecretPrompt(
                provider=provider,
                action=action,
                label=f"{_PROVIDER_LABELS[provider]} API Key",
                environment_variable=provider_environment_variable(provider),
                help_url=_PROVIDER_HELP_URLS[provider],
            ),
        )


def _provider(value: str) -> ProviderName | None:
    if value == "deepseek":
        return "deepseek"
    if value == "kimi":
        return "kimi"
    return None


def _status(
    sources: LoadedConfigSources,
    provider: ProviderName,
) -> ProviderCredentialStatus:
    if provider == "deepseek":
        return sources.provider_credentials.deepseek
    return sources.provider_credentials.kimi


def _error(code: str, content: str) -> CommandResult:
    return CommandResult(
        status=CommandStatus.ERROR,
        content=content,
        data=cast(dict[str, JsonValue], {"error_code": code}),
    )


def _managed_error(environment_variable: str) -> CommandResult:
    return _error(
        "credential_managed_by_environment",
        f"Credential is managed by {environment_variable} in the process environment.",
    )
