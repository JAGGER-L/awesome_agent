from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from awesome_agent.config import (
    ApplicationConfig,
    BudgetConfig,
    ConfigurationInvalid,
    ConfigurationResolutionError,
    CredentialSource,
    LoadedConfigSources,
    MemoryConfig,
    ProjectBudgetConfig,
    ProjectWebConfig,
    ProviderConfig,
    ProviderCredentialStatus,
    ProviderCredentialStatuses,
    SecretStatus,
    SecretValues,
    StartupOverrides,
    ThreadConfigState,
    UserBudgetConfig,
    UserConfigDocument,
    UserConfigWriter,
    WebConfig,
    WorkspaceConfigDocument,
    load_config_sources,
    missing_provider_credential_statuses,
    resolve_application_config,
    resolve_turn_config,
)
from awesome_agent.paths import AwesomePaths


def _application(
    *,
    default_model: str | None = None,
    deepseek: bool = False,
    kimi: bool = False,
    user_budgets: UserBudgetConfig | None = None,
    project_budgets: ProjectBudgetConfig | None = None,
) -> ApplicationConfig:
    from awesome_agent.config.loader import LoadedConfigSources, SecretValues

    return resolve_application_config(
        LoadedConfigSources(
            user=UserConfigDocument(
                providers=ProviderConfig(default_model=default_model),
                budgets=user_budgets or UserBudgetConfig(),
            ),
            workspace=WorkspaceConfigDocument(
                budgets=project_budgets or ProjectBudgetConfig()
            ),
            secrets=SecretValues(),
            secret_status=SecretStatus(
                deepseek_api_key=deepseek,
                moonshot_api_key=kimi,
            ),
            provider_credentials=ProviderCredentialStatuses(
                deepseek=ProviderCredentialStatus(
                    provider="deepseek",
                    environment_variable="DEEPSEEK_API_KEY",
                    environment_configured=False,
                    awesome_configured=deepseek,
                    selected_source=CredentialSource.AWESOME if deepseek else None,
                ),
                kimi=ProviderCredentialStatus(
                    provider="kimi",
                    environment_variable="MOONSHOT_API_KEY",
                    environment_configured=False,
                    awesome_configured=kimi,
                    selected_source=CredentialSource.AWESOME if kimi else None,
                ),
                mem0=ProviderCredentialStatus(
                    provider="mem0",
                    environment_variable="MEM0_API_KEY",
                    environment_configured=False,
                    awesome_configured=False,
                    selected_source=None,
                ),
            ),
        )
    )


@pytest.mark.parametrize(
    ("deepseek", "kimi", "expected_provider", "expected_model"),
    [
        (True, False, "deepseek", "deepseek/deepseek-v4-flash"),
        (False, True, "kimi", "kimi/kimi-k2.6"),
    ],
)
def test_only_configured_provider_supplies_its_default(
    deepseek: bool,
    kimi: bool,
    expected_provider: str,
    expected_model: str,
) -> None:
    turn = resolve_turn_config(
        _application(deepseek=deepseek, kimi=kimi),
        thread=ThreadConfigState(),
        environ={},
    )

    assert turn.provider == expected_provider
    assert turn.model == expected_model
    assert turn.thinking_enabled is True
    assert turn.skill_mode == "auto"


def test_two_configured_providers_require_explicit_model() -> None:
    with pytest.raises(ConfigurationResolutionError) as raised:
        resolve_turn_config(
            _application(deepseek=True, kimi=True),
            thread=ThreadConfigState(),
            environ={},
        )

    assert raised.value.code == "model_not_configured"


def test_model_selection_precedence_is_cli_env_thread_user_default() -> None:
    application = _application(
        default_model="deepseek/deepseek-v4-flash",
        deepseek=True,
        kimi=True,
    )
    thread = ThreadConfigState(model="kimi/kimi-k2.5")

    assert (
        resolve_turn_config(application, thread=thread, environ={}).model
        == "kimi/kimi-k2.5"
    )
    assert (
        resolve_turn_config(
            application,
            thread=thread,
            environ={"AWESOME_MODEL": "deepseek/deepseek-v4-pro"},
        ).model
        == "deepseek/deepseek-v4-pro"
    )
    assert (
        resolve_turn_config(
            application,
            thread=thread,
            cli=StartupOverrides(model="kimi/kimi-k2.6"),
            environ={"AWESOME_MODEL": "deepseek/deepseek-v4-pro"},
        ).model
        == "kimi/kimi-k2.6"
    )


def test_selected_model_requires_its_provider_credential() -> None:
    with pytest.raises(ConfigurationResolutionError) as raised:
        resolve_turn_config(
            _application(
                default_model="kimi/kimi-k2.6",
                deepseek=True,
                kimi=False,
            ),
            thread=ThreadConfigState(),
            environ={},
        )

    assert raised.value.code == "provider_not_configured"


def test_thinking_and_skill_use_cli_env_thread_then_defaults() -> None:
    application = _application(deepseek=True)
    thread = ThreadConfigState(thinking_enabled=True, skill_mode="debug")

    from_thread = resolve_turn_config(application, thread=thread, environ={})
    from_env = resolve_turn_config(
        application,
        thread=thread,
        environ={"AWESOME_THINKING": "off", "AWESOME_SKILL": "test"},
    )
    from_cli = resolve_turn_config(
        application,
        thread=thread,
        cli=StartupOverrides(thinking_enabled=True, skill_mode="off"),
        environ={"AWESOME_THINKING": "off", "AWESOME_SKILL": "test"},
    )

    assert (from_thread.thinking_enabled, from_thread.skill_mode) == (True, "debug")
    assert (from_env.thinking_enabled, from_env.skill_mode) == (False, "test")
    assert (from_cli.thinking_enabled, from_cli.skill_mode) == (True, "off")


def test_stored_thinking_off_wins_when_thread_is_resumed() -> None:
    turn = resolve_turn_config(
        _application(deepseek=True),
        thread=ThreadConfigState(thinking_enabled=False),
        environ={},
    )

    assert turn.thinking_enabled is False


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("AWESOME_MODEL", "not-a-model"),
        ("AWESOME_THINKING", "maybe"),
        ("AWESOME_SKILL", "../escape"),
    ],
)
def test_documented_selection_environment_rejects_invalid_values(
    name: str,
    value: str,
) -> None:
    with pytest.raises(ConfigurationResolutionError) as raised:
        resolve_turn_config(
            _application(deepseek=True),
            thread=ThreadConfigState(),
            environ={name: value},
        )

    assert raised.value.code == "configuration_invalid"


def test_explicit_empty_selection_overrides_fail_closed() -> None:
    application = _application(deepseek=True)

    with pytest.raises(ConfigurationResolutionError) as cli_error:
        resolve_turn_config(
            application,
            thread=ThreadConfigState(),
            cli=StartupOverrides(model=""),
            environ={},
        )
    with pytest.raises(ConfigurationResolutionError) as env_error:
        resolve_turn_config(
            application,
            thread=ThreadConfigState(),
            environ={"AWESOME_SKILL": ""},
        )

    assert cli_error.value.code == "configuration_invalid"
    assert env_error.value.code == "configuration_invalid"


def test_workspace_limits_can_only_reduce_user_limits() -> None:
    application = _application(
        deepseek=True,
        user_budgets=UserBudgetConfig(
            model_calls=32,
            tool_calls=64,
            provider_retries=2,
            compressions=2,
            active_execution_seconds=1_800,
            total_context_tokens=262_144,
            web_requests=8,
        ),
        project_budgets=ProjectBudgetConfig(
            model_calls=16,
            tool_calls=128,
            provider_retries=1,
            compressions=4,
            active_execution_seconds=900,
            total_context_tokens=200_000,
            web_requests=3,
        ),
    )
    turn = resolve_turn_config(
        application,
        thread=ThreadConfigState(),
        environ={},
    )

    assert application.budgets == BudgetConfig(
        model_calls=16,
        tool_calls=64,
        provider_retries=1,
        compressions=2,
        active_execution_seconds=900,
        total_context_tokens=200_000,
        web_requests=3,
    )
    assert application.budgets.web_requests == 3
    assert turn.budgets.total_context_tokens == 200_000


def test_selected_catalog_profile_caps_total_context_tokens() -> None:
    application = _application(
        deepseek=True,
        user_budgets=UserBudgetConfig(total_context_tokens=300_000),
    )

    turn = resolve_turn_config(
        application,
        thread=ThreadConfigState(),
        environ={},
    )

    assert turn.budgets.total_context_tokens == 262_144


def test_workspace_web_domains_only_add_restrictions() -> None:
    sources = LoadedConfigSources(
        user=UserConfigDocument(
            web=WebConfig(
                enabled=True,
                blocked_domains=("user.example",),
            )
        ),
        workspace=WorkspaceConfigDocument(
            web=ProjectWebConfig(
                blocked_domains=("workspace.example", "user.example")
            )
        ),
        secrets=SecretValues(),
        secret_status=SecretStatus(),
        provider_credentials=missing_provider_credential_statuses(),
    )

    application = resolve_application_config(sources)

    assert application.web.enabled is True
    assert application.web.blocked_domains == (
        "user.example",
        "workspace.example",
    )


def test_effective_web_domain_restrictions_remain_bounded() -> None:
    sources = LoadedConfigSources(
        user=UserConfigDocument(
            web=WebConfig(blocked_domains=("user.example",))
        ),
        workspace=WorkspaceConfigDocument(
            web=ProjectWebConfig(
                blocked_domains=tuple(
                    f"workspace-{index}.example" for index in range(128)
                )
            )
        ),
        secrets=SecretValues(),
        secret_status=SecretStatus(),
        provider_credentials=missing_provider_credential_statuses(),
    )

    with pytest.raises(ConfigurationResolutionError) as caught:
        resolve_application_config(sources)

    assert caught.value.code == "configuration_invalid"


def test_hard_budget_limits_fail_during_source_validation() -> None:
    with pytest.raises(ValidationError):
        BudgetConfig(model_calls=257)
    with pytest.raises(ValidationError):
        BudgetConfig(tool_calls=513)
    with pytest.raises(ValidationError):
        BudgetConfig(provider_retries=7)
    with pytest.raises(ValidationError):
        BudgetConfig(compressions=11)
    with pytest.raises(ValidationError):
        BudgetConfig(active_execution_seconds=21_601)
    with pytest.raises(ValidationError):
        UserBudgetConfig(web_requests=9)
    with pytest.raises(ValidationError):
        ProjectBudgetConfig(web_requests=9)


def test_application_snapshot_does_not_change_after_source_edit(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    config_path = home / "config.yaml"
    config_path.write_text(
        "budgets:\n  model_calls: 12\n",
        encoding="utf-8",
    )
    paths = AwesomePaths.from_home(home)
    loaded = load_config_sources(
        paths=paths,
        workspace=tmp_path / "workspace",
        workspace_trusted=False,
        environ={"DEEPSEEK_API_KEY": "secret"},
    )
    application = resolve_application_config(loaded)

    config_path.write_text("budgets:\n  model_calls: 4\n", encoding="utf-8")

    assert application.budgets.model_calls == 12


def test_user_config_writer_updates_atomically_and_never_writes_secrets(
    tmp_path: Path,
) -> None:
    path = tmp_path / "home" / "config.yaml"
    writer = UserConfigWriter(path)

    updated = writer.update(
        lambda current: current.model_copy(
            update={
                "memory": MemoryConfig(
                    local_file_memory=True,
                    mem0_cloud=current.memory.mem0_cloud,
                    mem0_user_id=current.memory.mem0_user_id,
                )
            }
        )
    )

    assert updated.memory.local_file_memory is True
    content = path.read_text(encoding="utf-8")
    assert "local_file_memory: true" in content
    assert "API_KEY" not in content
    assert list(path.parent.glob(f".{path.name}.*.tmp")) == []


def test_first_supported_write_atomically_upgrades_user_v1_to_v2(
    tmp_path: Path,
) -> None:
    path = tmp_path / "home" / "config.yaml"
    path.parent.mkdir()
    path.write_text(
        "version: 1\nbudgets:\n  model_calls: 12\n",
        encoding="utf-8",
    )

    updated = UserConfigWriter(path).update(lambda current: current)

    content = path.read_text(encoding="utf-8")
    assert updated.version == 2
    assert content.startswith("version: 2\n")
    assert "tavily: environment" in content
    assert "web_proxy: null" in content
    assert "web_requests: 8" in content
    assert "enabled: false" in content
    assert list(path.parent.glob(f".{path.name}.*.tmp")) == []


def test_user_config_writer_refuses_unknown_existing_fields(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("unknown: true\n", encoding="utf-8")

    with pytest.raises(ConfigurationInvalid):
        UserConfigWriter(path).update(lambda current: current)


def test_user_config_writer_keeps_original_when_transform_fails(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    original = "budgets:\n  model_calls: 12\n"
    path.write_text(original, encoding="utf-8")

    def fail(_: UserConfigDocument) -> UserConfigDocument:
        raise RuntimeError("stop")

    with pytest.raises(RuntimeError, match="stop"):
        UserConfigWriter(path).update(fail)

    assert path.read_text(encoding="utf-8") == original


def test_user_config_writer_revalidates_transform_output(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    original = "budgets:\n  model_calls: 12\n"
    path.write_text(original, encoding="utf-8")

    with pytest.raises(ValidationError):
        UserConfigWriter(path).update(
            lambda current: current.model_copy(
                update={
                    "budgets": current.budgets.model_copy(update={"model_calls": 257})
                }
            )
        )

    assert path.read_text(encoding="utf-8") == original
