from __future__ import annotations

import pytest
from pydantic import ValidationError

from awesome_agent.config import KimiRegion, ProviderConfig
from awesome_agent.modeling import (
    MODEL_CATALOG,
    ModelCatalog,
    ModelCatalogError,
    ModelProfile,
    ProviderDescriptor,
    ProviderId,
    SelectedModel,
)


def test_catalog_contains_exactly_two_providers_and_four_curated_models() -> None:
    assert MODEL_CATALOG.provider_ids() == ("deepseek", "kimi")
    assert MODEL_CATALOG.model_ids() == (
        "deepseek/deepseek-v4-flash",
        "deepseek/deepseek-v4-pro",
        "kimi/kimi-k2.6",
        "kimi/kimi-k2.5",
    )
    profiles = tuple(
        profile
        for descriptor in MODEL_CATALOG.providers
        for profile in descriptor.models
    )
    assert all(profile.context_limit == 262_144 for profile in profiles)
    assert all(profile.supports_tools for profile in profiles)
    assert all(profile.supports_reasoning for profile in profiles)


def test_provider_descriptors_publish_credentials_regions_and_models() -> None:
    deepseek = MODEL_CATALOG.provider("deepseek")
    kimi = MODEL_CATALOG.provider("kimi")

    assert deepseek.credential_id == "deepseek"
    assert deepseek.supported_regions == ()
    assert deepseek.default_region is None
    assert tuple(profile.id for profile in deepseek.models) == (
        "deepseek/deepseek-v4-flash",
        "deepseek/deepseek-v4-pro",
    )
    assert kimi.credential_id == "kimi"
    assert kimi.supported_regions == (KimiRegion.CN, KimiRegion.GLOBAL)
    assert kimi.default_region is KimiRegion.CN
    assert tuple(profile.id for profile in kimi.models) == (
        "kimi/kimi-k2.6",
        "kimi/kimi-k2.5",
    )


def test_each_provider_has_one_catalog_derived_default() -> None:
    assert MODEL_CATALOG.default_for("deepseek") == ("deepseek/deepseek-v4-flash")
    assert MODEL_CATALOG.default_for("kimi") == "kimi/kimi-k2.6"


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        (
            ("deepseek",),
            SelectedModel(provider="deepseek", model="deepseek/deepseek-v4-flash"),
        ),
        (("kimi",), SelectedModel(provider="kimi", model="kimi/kimi-k2.6")),
    ],
)
def test_only_configured_provider_supplies_default_selection(
    configured: tuple[ProviderId, ...],
    expected: SelectedModel,
) -> None:
    selected = MODEL_CATALOG.require_selection(configured_providers=configured)

    assert selected == expected


def test_two_credentials_without_selection_are_ambiguous() -> None:
    with pytest.raises(ModelCatalogError) as raised:
        MODEL_CATALOG.require_selection(
            configured_providers=("deepseek", "kimi"),
        )

    assert raised.value.code == "model_not_configured"


def test_selected_model_requires_matching_provider_credential() -> None:
    with pytest.raises(ModelCatalogError) as raised:
        MODEL_CATALOG.require_selection(
            "kimi/kimi-k2.6",
            configured_providers=("deepseek",),
        )

    assert raised.value.code == "provider_not_configured"
    assert raised.value.hint == "Use /auth kimi to configure credentials."


@pytest.mark.parametrize(
    "model",
    ["deepseek-v4-flash", "custom/model", "openai/gpt-5", "kimi/custom"],
)
def test_unqualified_or_custom_model_ids_are_rejected(model: str) -> None:
    with pytest.raises(ModelCatalogError) as raised:
        MODEL_CATALOG.require_selection(
            model,
            configured_providers=("deepseek", "kimi"),
        )

    assert raised.value.code == "unsupported_model"


def test_explicit_selection_returns_only_provider_and_model() -> None:
    selected = MODEL_CATALOG.require_selection(
        "kimi/kimi-k2.5",
        configured_providers=("deepseek", "kimi"),
    )

    assert selected == SelectedModel(provider="kimi", model="kimi/kimi-k2.5")
    assert selected.model_dump(mode="json") == {
        "provider": "kimi",
        "model": "kimi/kimi-k2.5",
    }


def test_selected_model_rejects_cross_provider_model_pair() -> None:
    with pytest.raises(ValidationError, match="does not belong"):
        SelectedModel(provider="deepseek", model="kimi/kimi-k2.6")


def test_provider_descriptor_rejects_invalid_nested_contracts() -> None:
    profile = ModelProfile(
        id="kimi/kimi-k2.6",
        context_limit=262_144,
        supports_tools=True,
        supports_reasoning=True,
        is_default=True,
    )

    with pytest.raises(ValidationError, match="prefix"):
        ProviderDescriptor(id="deepseek", credential_id="deepseek", models=(profile,))
    with pytest.raises(ValidationError, match="supported regions"):
        ProviderDescriptor(
            id="kimi",
            credential_id="kimi",
            supported_regions=(KimiRegion.CN,),
            default_region=KimiRegion.GLOBAL,
            models=(profile,),
        )


def test_catalog_rejects_duplicate_provider_identifiers() -> None:
    descriptor = MODEL_CATALOG.provider("deepseek")

    with pytest.raises(ValidationError, match="Provider identifiers"):
        ModelCatalog(providers=(descriptor, descriptor))


def test_kimi_region_is_closed_to_cn_and_global() -> None:
    with pytest.raises(ValidationError):
        ProviderConfig.model_validate({"kimi_region": "custom"})
