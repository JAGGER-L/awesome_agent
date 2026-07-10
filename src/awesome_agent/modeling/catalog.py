from __future__ import annotations

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from awesome_agent.config.models import ApplicationConfig, KimiRegion
from awesome_agent.modeling.turns import ProviderId

DEEPSEEK_DEFAULT_MODEL = "deepseek/deepseek-v4-flash"
KIMI_DEFAULT_MODEL = "kimi/kimi-k2.6"

_EXPECTED_MODELS = (
    DEEPSEEK_DEFAULT_MODEL,
    "deepseek/deepseek-v4-pro",
    KIMI_DEFAULT_MODEL,
    "kimi/kimi-k2.5",
)
_CREDENTIAL_ENV = {
    "deepseek": "DEEPSEEK_API_KEY",
    "kimi": "MOONSHOT_API_KEY",
}


class ModelCatalogError(ValueError):
    def __init__(self, code: str, message: str, *, hint: str = "") -> None:
        self.code = code
        self.message = message
        self.hint = hint
        super().__init__(message)


class SelectedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: ProviderId
    model: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def validate_provider_model_pair(self) -> Self:
        if self.model not in _EXPECTED_MODELS or not self.model.startswith(
            f"{self.provider}/"
        ):
            raise ValueError("Selected model does not belong to its Provider.")
        return self


class ModelProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=200)
    provider: ProviderId
    context_limit: int = Field(ge=1)
    supports_tools: bool
    supports_reasoning: bool
    is_default: bool = False


class ModelCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    models: tuple[ModelProfile, ...]
    configured_providers: tuple[ProviderId, ...] = ()
    default_model: str | None = None
    kimi_region: KimiRegion = KimiRegion.CN

    @model_validator(mode="after")
    def validate_closed_catalog(self) -> Self:
        if tuple(profile.id for profile in self.models) != _EXPECTED_MODELS:
            raise ValueError("Model catalog must contain the exact curated models.")
        if len(self.configured_providers) != len(set(self.configured_providers)):
            raise ValueError("Configured Providers must be unique.")
        return self

    @classmethod
    def from_application(cls, application: ApplicationConfig) -> ModelCatalog:
        configured: list[ProviderId] = []
        if application.secret_status.deepseek_api_key:
            configured.append("deepseek")
        if application.secret_status.moonshot_api_key:
            configured.append("kimi")
        return cls(
            models=(
                _profile(DEEPSEEK_DEFAULT_MODEL, "deepseek", default=True),
                _profile("deepseek/deepseek-v4-pro", "deepseek"),
                _profile(KIMI_DEFAULT_MODEL, "kimi", default=True),
                _profile("kimi/kimi-k2.5", "kimi"),
            ),
            configured_providers=tuple(configured),
            default_model=application.providers.default_model,
            kimi_region=application.providers.kimi_region,
        )

    def provider_ids(self) -> tuple[ProviderId, ...]:
        return ("deepseek", "kimi")

    def model_ids(self) -> tuple[str, ...]:
        return tuple(profile.id for profile in self.models)

    def default_for(self, provider: ProviderId) -> str:
        return DEEPSEEK_DEFAULT_MODEL if provider == "deepseek" else KIMI_DEFAULT_MODEL

    def profile(self, model: str) -> ModelProfile:
        profile = next((item for item in self.models if item.id == model), None)
        if profile is None:
            raise ModelCatalogError(
                "unsupported_model",
                "Selected model is not in the curated catalog.",
                hint="Choose one of the models returned by /model.",
            )
        return profile

    def require_selection(self, model: str | None = None) -> SelectedModel:
        candidate = model if model is not None else self.default_model
        if candidate is None:
            if len(self.configured_providers) != 1:
                raise ModelCatalogError(
                    "model_not_configured",
                    "Select a Provider/model before starting an Agent Turn.",
                    hint="Use /model to select DeepSeek or Kimi.",
                )
            provider = self.configured_providers[0]
            candidate = self.default_for(provider)
        profile = self.profile(candidate)
        if profile.provider not in self.configured_providers:
            credential = _CREDENTIAL_ENV[profile.provider]
            raise ModelCatalogError(
                "provider_not_configured",
                f"{profile.provider} credentials are not configured.",
                hint=f"Set {credential} in the user secret environment.",
            )
        return SelectedModel(provider=profile.provider, model=profile.id)


def _profile(
    model: str,
    provider: ProviderId,
    *,
    default: bool = False,
) -> ModelProfile:
    return ModelProfile(
        id=model,
        provider=provider,
        context_limit=262_144,
        supports_tools=True,
        supports_reasoning=True,
        is_default=default,
    )
