from __future__ import annotations

from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from awesome_agent.modeling.turns import ProviderId


class KimiRegion(StrEnum):
    CN = "cn"
    GLOBAL = "global"


class ModelCatalogError(ValueError):
    def __init__(self, code: str, message: str, *, hint: str = "") -> None:
        self.code = code
        self.message = message
        self.hint = hint
        super().__init__(message)


class ModelProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=200)
    context_limit: int = Field(ge=1)
    supports_tools: bool
    supports_reasoning: bool
    is_default: bool = False


class ProviderDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: ProviderId
    credential_id: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_-]{0,63}$",
    )
    supported_regions: tuple[KimiRegion, ...] = ()
    default_region: KimiRegion | None = None
    models: tuple[ModelProfile, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_descriptor(self) -> Self:
        model_ids = tuple(profile.id for profile in self.models)
        if len(model_ids) != len(set(model_ids)):
            raise ValueError("Provider model identifiers must be unique.")
        if any(not model.startswith(f"{self.id}/") for model in model_ids):
            raise ValueError("Provider models must use their Provider id prefix.")
        if sum(profile.is_default for profile in self.models) != 1:
            raise ValueError("Each Provider must declare exactly one default model.")
        if len(self.supported_regions) != len(set(self.supported_regions)):
            raise ValueError("Provider regions must be unique.")
        if self.supported_regions:
            if self.default_region not in self.supported_regions:
                raise ValueError(
                    "A regional Provider default must be one of its supported regions."
                )
        elif self.default_region is not None:
            raise ValueError("A non-regional Provider cannot declare a default region.")
        return self


class ModelCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    providers: tuple[ProviderDescriptor, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_catalog(self) -> Self:
        provider_ids = self.provider_ids()
        if len(provider_ids) != len(set(provider_ids)):
            raise ValueError("Model Catalog Provider identifiers must be unique.")
        model_ids = self.model_ids()
        if len(model_ids) != len(set(model_ids)):
            raise ValueError("Model Catalog model identifiers must be unique.")
        return self

    def provider_ids(self) -> tuple[ProviderId, ...]:
        return tuple(provider.id for provider in self.providers)

    def model_ids(self) -> tuple[str, ...]:
        return tuple(
            profile.id for provider in self.providers for profile in provider.models
        )

    def provider(self, provider_id: str) -> ProviderDescriptor:
        descriptor = next(
            (provider for provider in self.providers if provider.id == provider_id),
            None,
        )
        if descriptor is None:
            raise ModelCatalogError(
                "unsupported_provider",
                "Selected Provider is not in the curated catalog.",
                hint="Choose one of the Providers returned by /model.",
            )
        return descriptor

    def profile(self, model: str) -> ModelProfile:
        for provider in self.providers:
            profile = next((item for item in provider.models if item.id == model), None)
            if profile is not None:
                return profile
        raise ModelCatalogError(
            "unsupported_model",
            "Selected model is not in the curated catalog.",
            hint="Choose one of the models returned by /model.",
        )

    def provider_for_model(self, model: str) -> ProviderDescriptor:
        for provider in self.providers:
            if any(profile.id == model for profile in provider.models):
                return provider
        raise ModelCatalogError(
            "unsupported_model",
            "Selected model is not in the curated catalog.",
            hint="Choose one of the models returned by /model.",
        )

    def models_for(self, provider_id: str) -> tuple[ModelProfile, ...]:
        return self.provider(provider_id).models

    def default_for(self, provider_id: str) -> str:
        descriptor = self.provider(provider_id)
        return next(profile.id for profile in descriptor.models if profile.is_default)

    def require_selection(
        self,
        model: str | None = None,
        *,
        configured_providers: tuple[ProviderId, ...],
    ) -> SelectedModel:
        if len(configured_providers) != len(set(configured_providers)):
            raise ValueError("Configured Providers must be unique.")
        for provider_id in configured_providers:
            self.provider(provider_id)
        candidate = model
        if candidate is None:
            if len(configured_providers) != 1:
                raise ModelCatalogError(
                    "model_not_configured",
                    "Select a Provider/model before starting an Agent Turn.",
                    hint="Use /model to select a supported Provider.",
                )
            candidate = self.default_for(configured_providers[0])
        provider = self.provider_for_model(candidate)
        if provider.id not in configured_providers:
            raise ModelCatalogError(
                "provider_not_configured",
                f"{provider.id} credentials are not configured.",
                hint=f"Use /auth {provider.id} to configure credentials.",
            )
        return SelectedModel(provider=provider.id, model=candidate)


MODEL_CATALOG = ModelCatalog(
    providers=(
        ProviderDescriptor(
            id="deepseek",
            credential_id="deepseek",
            models=(
                ModelProfile(
                    id="deepseek/deepseek-v4-flash",
                    context_limit=262_144,
                    supports_tools=True,
                    supports_reasoning=True,
                    is_default=True,
                ),
                ModelProfile(
                    id="deepseek/deepseek-v4-pro",
                    context_limit=262_144,
                    supports_tools=True,
                    supports_reasoning=True,
                ),
            ),
        ),
        ProviderDescriptor(
            id="kimi",
            credential_id="kimi",
            supported_regions=(KimiRegion.CN, KimiRegion.GLOBAL),
            default_region=KimiRegion.CN,
            models=(
                ModelProfile(
                    id="kimi/kimi-k2.6",
                    context_limit=262_144,
                    supports_tools=True,
                    supports_reasoning=True,
                    is_default=True,
                ),
                ModelProfile(
                    id="kimi/kimi-k2.5",
                    context_limit=262_144,
                    supports_tools=True,
                    supports_reasoning=True,
                ),
            ),
        ),
    )
)


class SelectedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: ProviderId
    model: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def validate_provider_model_pair(self) -> Self:
        try:
            provider = MODEL_CATALOG.provider_for_model(self.model)
        except ModelCatalogError as error:
            raise ValueError("Selected model is not in the curated catalog.") from error
        if provider.id != self.provider:
            raise ValueError("Selected model does not belong to its Provider.")
        return self


class ModelIdentitySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: ProviderId
    configured_model: str = Field(min_length=1, max_length=200)
    effective_model: str = Field(min_length=1, max_length=200)
    runtime_name: Literal["Awesome Agent"] = "Awesome Agent"
    fallback_active: bool
    fallback_from: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        try:
            MODEL_CATALOG.profile(self.configured_model)
            provider = MODEL_CATALOG.provider_for_model(self.effective_model)
        except ModelCatalogError as error:
            raise ValueError("Model identity is not in the curated catalog.") from error
        if provider.id != self.provider:
            raise ValueError("Effective model does not belong to its Provider.")
        expected_fallback = self.configured_model != self.effective_model
        if self.fallback_active is not expected_fallback:
            raise ValueError("Fallback state does not match model identity.")
        expected_from = self.configured_model if expected_fallback else None
        if self.fallback_from != expected_from:
            raise ValueError("Fallback source does not match configured model.")
        return self

    @classmethod
    def from_models(
        cls,
        *,
        configured_model: str,
        effective_model: str,
    ) -> ModelIdentitySnapshot:
        provider = MODEL_CATALOG.provider_for_model(effective_model)
        fallback_active = configured_model != effective_model
        return cls(
            provider=provider.id,
            configured_model=configured_model,
            effective_model=effective_model,
            fallback_active=fallback_active,
            fallback_from=configured_model if fallback_active else None,
        )
