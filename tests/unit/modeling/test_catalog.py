from __future__ import annotations

import pytest
from pydantic import ValidationError

from awesome_agent.config import (
    ApplicationConfig,
    BudgetConfig,
    KimiRegion,
    MemoryConfig,
    ProviderConfig,
    SecretStatus,
)
from awesome_agent.modeling import ModelCatalog, ModelCatalogError, SelectedModel


def _application(
    *,
    deepseek: bool = False,
    kimi: bool = False,
    default_model: str | None = None,
    region: KimiRegion = KimiRegion.CN,
) -> ApplicationConfig:
    return ApplicationConfig(
        providers=ProviderConfig(default_model=default_model, kimi_region=region),
        budgets=BudgetConfig(),
        memory=MemoryConfig(),
        secret_status=SecretStatus(
            deepseek_api_key=deepseek,
            moonshot_api_key=kimi,
        ),
    )


def test_catalog_contains_exactly_two_providers_and_four_curated_models() -> None:
    catalog = ModelCatalog.from_application(_application())

    assert catalog.provider_ids() == ("deepseek", "kimi")
    assert catalog.model_ids() == (
        "deepseek/deepseek-v4-flash",
        "deepseek/deepseek-v4-pro",
        "kimi/kimi-k2.6",
        "kimi/kimi-k2.5",
    )
    assert all(profile.context_limit > 0 for profile in catalog.models)
    assert all(profile.supports_tools for profile in catalog.models)
    assert all(profile.supports_reasoning for profile in catalog.models)


def test_each_provider_has_one_fixed_default() -> None:
    catalog = ModelCatalog.from_application(_application())

    assert catalog.default_for("deepseek") == "deepseek/deepseek-v4-flash"
    assert catalog.default_for("kimi") == "kimi/kimi-k2.6"


@pytest.mark.parametrize(
    ("deepseek", "kimi", "expected"),
    [
        (
            True,
            False,
            SelectedModel(provider="deepseek", model="deepseek/deepseek-v4-flash"),
        ),
        (False, True, SelectedModel(provider="kimi", model="kimi/kimi-k2.6")),
    ],
)
def test_only_configured_provider_supplies_default_selection(
    deepseek: bool,
    kimi: bool,
    expected: SelectedModel,
) -> None:
    catalog = ModelCatalog.from_application(_application(deepseek=deepseek, kimi=kimi))

    assert catalog.require_selection() == expected


def test_two_credentials_without_selection_are_ambiguous() -> None:
    catalog = ModelCatalog.from_application(_application(deepseek=True, kimi=True))

    with pytest.raises(ModelCatalogError) as raised:
        catalog.require_selection()

    assert raised.value.code == "model_not_configured"


def test_selected_model_requires_matching_provider_credential() -> None:
    catalog = ModelCatalog.from_application(_application(deepseek=True))

    with pytest.raises(ModelCatalogError) as raised:
        catalog.require_selection("kimi/kimi-k2.6")

    assert raised.value.code == "provider_not_configured"
    assert "MOONSHOT_API_KEY" in raised.value.hint


@pytest.mark.parametrize(
    "model",
    ["deepseek-v4-flash", "custom/model", "openai/gpt-5", "kimi/custom"],
)
def test_unqualified_or_custom_model_ids_are_rejected(model: str) -> None:
    catalog = ModelCatalog.from_application(_application(deepseek=True, kimi=True))

    with pytest.raises(ModelCatalogError) as raised:
        catalog.require_selection(model)

    assert raised.value.code == "unsupported_model"


def test_explicit_selection_returns_only_provider_and_model() -> None:
    catalog = ModelCatalog.from_application(
        _application(deepseek=True, kimi=True, region=KimiRegion.GLOBAL)
    )

    selected = catalog.require_selection("kimi/kimi-k2.5")

    assert selected == SelectedModel(provider="kimi", model="kimi/kimi-k2.5")
    assert selected.model_dump(mode="json") == {
        "provider": "kimi",
        "model": "kimi/kimi-k2.5",
    }
    assert catalog.kimi_region is KimiRegion.GLOBAL


def test_kimi_region_is_closed_to_cn_and_global() -> None:
    with pytest.raises(ValidationError):
        ProviderConfig(kimi_region="custom")
