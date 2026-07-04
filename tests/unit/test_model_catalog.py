from __future__ import annotations

import pytest
from tests.type_helpers import test_settings

from awesome_agent.modeling.catalog import (
    DEEPSEEK_OFFICIAL_BASE_URL,
    ModelCatalog,
    ModelCatalogError,
)


def test_model_catalog_is_deepseek_only() -> None:
    catalog = ModelCatalog.from_settings(test_settings(deepseek_api_key="secret"))

    providers = catalog.providers()

    assert [provider.id for provider in providers] == ["deepseek"]
    assert providers[0].display_name == "DeepSeek"
    assert providers[0].configured is True
    assert providers[0].credential_env == "AWESOME_AGENT_DEEPSEEK_API_KEY"
    assert {model.id for model in providers[0].models} == {
        "deepseek-v4-pro",
        "deepseek-v4-flash",
    }
    assert "openai" not in {provider.id for provider in providers}


def test_model_catalog_reports_missing_key_without_failing_models() -> None:
    catalog = ModelCatalog.from_settings(test_settings(deepseek_api_key=None))

    [provider] = catalog.providers()

    assert provider.id == "deepseek"
    assert provider.configured is False
    assert provider.api_key_present is False
    assert [model.id for model in provider.models] == [
        "deepseek-v4-pro",
        "deepseek-v4-flash",
    ]


def test_model_catalog_rejects_unknown_model() -> None:
    catalog = ModelCatalog.from_settings(test_settings(deepseek_api_key="secret"))

    with pytest.raises(ModelCatalogError) as raised:
        catalog.require_model("gpt-4o")

    assert raised.value.code == "unsupported_model"
    assert "gpt-4o" in str(raised.value)


def test_model_catalog_rejects_unknown_provider() -> None:
    catalog = ModelCatalog.from_settings(test_settings(deepseek_api_key="secret"))

    with pytest.raises(ModelCatalogError) as raised:
        catalog.require_provider("openai")

    assert raised.value.code == "unsupported_provider"


def test_model_catalog_rejects_custom_deepseek_base_url() -> None:
    catalog = ModelCatalog.from_settings(
        test_settings(
            deepseek_api_key="secret",
            deepseek_base_url="https://gateway.local/v1",
        )
    )

    with pytest.raises(ModelCatalogError) as raised:
        catalog.require_supported_configuration()

    assert raised.value.code == "unsupported_provider_configuration"
    assert raised.value.hint == (
        f"Use the official DeepSeek endpoint: {DEEPSEEK_OFFICIAL_BASE_URL}."
    )


def test_model_catalog_validates_role_models_and_overrides() -> None:
    catalog = ModelCatalog.from_settings(
        test_settings(
            deepseek_api_key="secret",
            leader_model="deepseek-v4-pro",
            teammate_model="deepseek-v4-flash",
            verifier_model="deepseek-v4-flash",
            subagent_model="deepseek-v4-flash",
            role_model_overrides={"reviewer": "deepseek-v4-pro"},
        )
    )

    catalog.validate_role_models()


def test_model_catalog_rejects_invalid_role_model() -> None:
    catalog = ModelCatalog.from_settings(
        test_settings(
            deepseek_api_key="secret",
            leader_model="gpt-4o",
        )
    )

    with pytest.raises(ModelCatalogError) as raised:
        catalog.validate_role_models()

    assert raised.value.code == "invalid_role_model"
    assert "leader" in str(raised.value)
