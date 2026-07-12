from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

import pytest
from dotenv import dotenv_values
from pydantic import SecretStr

from awesome_agent.application.commands import CommandIntent, CommandName
from awesome_agent.application.contracts import (
    ProviderCredentialSetRequest,
    ProviderCredentialSetStatus,
)
from awesome_agent.application.provider_configuration import (
    ProviderConfigurationService,
    ProviderCredentialManagedExternally,
)
from awesome_agent.config import (
    CredentialSource,
    CredentialValidation,
    CredentialValidationStatus,
    LoadedConfigSources,
    UserConfigWriter,
    UserSecretStore,
    load_config_sources,
)
from awesome_agent.conversation import ConversationService
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

    service = ProviderConfigurationService(
        conversation=conversation,
        config_writer=UserConfigWriter(paths.config_file),
        secret_store=UserSecretStore(paths.env_file),
        validator=validator,
        sources=sources,
        reload_configuration=lambda: None,
    )
    return service, conversation, sources


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

    assert providers.selection is not None
    assert [option.value for option in providers.selection.options] == [
        "deepseek",
        "kimi",
    ]
    assert [option.description for option in providers.selection.options] == [
        "Not configured",
        "Not configured",
    ]
    assert missing.secret_prompt is not None
    assert missing.secret_prompt.provider == "deepseek"
    assert missing.secret_prompt.action == "add"


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
            api_key=SecretStr("new-secret"),
        )
    )
    models = await service.model_command(
        CommandIntent(name=CommandName.MODEL, arguments=("deepseek",)),
        thread_id=thread.id,
    )

    assert saved.status is ProviderCredentialSetStatus.SAVED
    assert saved.source is CredentialSource.USER_ENV_FILE
    assert sources().provider_credentials.deepseek.configured is True
    assert models.selection is not None
    assert [option.value for option in models.selection.options] == [
        "deepseek/deepseek-v4-flash",
        "deepseek/deepseek-v4-pro",
    ]


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
        api_key=SecretStr("unverified-secret"),
    )

    confirmation = await service.set_credential(request)
    assert confirmation.status is ProviderCredentialSetStatus.CONFIRM_UNVERIFIED
    assert not (tmp_path / "home" / ".env").exists()

    saved = await service.set_credential(
        request.model_copy(update={"allow_unverified": True})
    )
    assert saved.status is ProviderCredentialSetStatus.SAVED
    assert dotenv_values(tmp_path / "home" / ".env")["MOONSHOT_API_KEY"] == (
        "unverified-secret"
    )


@pytest.mark.asyncio
async def test_process_environment_credentials_are_not_mutated(tmp_path: Path) -> None:
    service, _, _ = _service(
        tmp_path,
        validator=FakeValidator(CredentialValidationStatus.VALID),
        environ={"DEEPSEEK_API_KEY": "external-secret"},
    )

    with pytest.raises(ProviderCredentialManagedExternally):
        await service.set_credential(
            ProviderCredentialSetRequest(
                provider="deepseek",
                api_key=SecretStr("replacement"),
            )
        )
    result = await service.auth_command(
        CommandIntent(
            name=CommandName.AUTH,
            arguments=("deepseek", "remove", "confirm"),
        )
    )

    assert result.status == "error"
    assert result.data["error_code"] == "credential_managed_by_environment"
    assert not (tmp_path / "home" / ".env").exists()


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

    assert result.data == {
        "model": "kimi/kimi-k2.6",
        "default_model_updated": True,
    }
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
