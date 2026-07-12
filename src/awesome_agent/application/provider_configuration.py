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
    CredentialService,
    CredentialSource,
    CredentialValidation,
    CredentialValidationStatus,
    KimiRegion,
    LoadedConfigSources,
    ProviderCredentialStatus,
    ProviderName,
    UserConfigWriter,
    UserSecretStore,
    provider_environment_variable,
)
from awesome_agent.conversation import ConversationService, ThreadNotFound

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
                    prompt="Authentication",
                    options=self._service_options(),
                ),
            )
        service = _service(arguments[0])
        if service is None:
            return _error("invalid_arguments", "Usage: /auth [deepseek|kimi|mem0]")
        status = _status(self._sources(), service)
        if len(arguments) == 1:
            return CommandResult(
                status=CommandStatus.SUCCESS,
                selection=CommandSelection(
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
            self._select_source(service, CredentialSource.ENVIRONMENT)
            return CommandResult(
                status=CommandStatus.SUCCESS,
                content=f"{_SERVICE_LABELS[service]} now uses Environment.",
            )
        if source != CredentialSource.AWESOME.value:
            return _error("invalid_arguments", "Choose Environment or Awesome API key.")
        if len(arguments) == 2:
            if not status.awesome_configured:
                return self._secret_prompt(service, action="add")
            return CommandResult(
                status=CommandStatus.SUCCESS,
                selection=CommandSelection(
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
            self._select_source(service, CredentialSource.AWESOME)
            return CommandResult(
                status=CommandStatus.SUCCESS,
                content=f"{_SERVICE_LABELS[service]} now uses Awesome API key.",
            )
        if len(arguments) == 3 and action == "replace":
            return self._secret_prompt(service, action="replace")
        if len(arguments) == 3 and action == "delete":
            return CommandResult(
                status=CommandStatus.SUCCESS,
                selection=CommandSelection(
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
        if request.action == "delete":
            self._secret_store.delete(status.environment_variable)
            self._reload_configuration()
            return ProviderCredentialSetResult(
                provider=request.provider,
                status=ProviderCredentialSetStatus.DELETED,
                source=status.selected_source,
                code="credential_deleted",
            )
        assert request.api_key is not None
        result = (
            CredentialValidation(
                status=CredentialValidationStatus.VALID, code="credential_valid"
            )
            if request.provider == "mem0"
            else await self._validator.validate(
                request.provider,
                request.api_key,
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
        self._secret_store.set(status.environment_variable, request.api_key)
        self._select_source(request.provider, CredentialSource.AWESOME)
        self._reload_configuration()
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
            active = (
                status.selected_source.value
                if status.selected_source
                else "Not configured"
            )
            options.append(
                CommandOption(
                    value=service,
                    label=_SERVICE_LABELS[service],
                    description=f"Active · {active}"
                    if status.configured
                    else "Not configured",
                )
            )
        return tuple(options)

    def _provider_options(self) -> tuple[CommandOption, ...]:
        return tuple(
            option for option in self._service_options() if option.value != "mem0"
        )

    def _select_source(
        self,
        service: CredentialService,
        source: CredentialSource,
    ) -> None:
        self._config_writer.update(
            lambda current: current.model_copy(
                update={
                    "credentials": current.credentials.model_copy(
                        update={service: source}
                    )
                }
            )
        )
        self._reload_configuration()

    def _secret_prompt(
        self,
        provider: CredentialService,
        *,
        action: Literal["add", "replace"],
    ) -> CommandResult:
        return CommandResult(
            status=CommandStatus.SUCCESS,
            secret_prompt=CommandSecretPrompt(
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


def _error(code: str, content: str) -> CommandResult:
    return CommandResult(
        status=CommandStatus.ERROR,
        content=content,
        data=cast(dict[str, JsonValue], {"error_code": code}),
    )
