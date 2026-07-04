from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, Field

from awesome_agent.settings import Settings

DEEPSEEK_PROVIDER_ID = "deepseek"
DEEPSEEK_DISPLAY_NAME = "DeepSeek"
DEEPSEEK_API_KEY_ENV = "AWESOME_AGENT_DEEPSEEK_API_KEY"
DEEPSEEK_OFFICIAL_BASE_URL = "https://api.deepseek.com"


class ModelCapability(StrEnum):
    STREAMING = "streaming"
    TOOLS = "tools"
    REASONING = "reasoning"


class ModelCatalogError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        hint: str | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.hint = hint
        super().__init__(message)


class ModelProfile(BaseModel):
    id: str
    display_name: str
    provider_id: str = DEEPSEEK_PROVIDER_ID
    capabilities: list[ModelCapability] = Field(default_factory=list)
    recommended_for: list[str] = Field(default_factory=list)
    selected: bool = False


class ProviderProfile(BaseModel):
    id: str
    display_name: str
    configured: bool
    credential_env: str
    api_key_present: bool
    models: list[ModelProfile] = Field(default_factory=list)


@dataclass(frozen=True, slots=True)
class SelectedModel:
    provider_id: str
    model_id: str


class ModelCatalog(BaseModel):
    providers_by_id: dict[str, ProviderProfile]
    current: SelectedModel
    role_models: dict[str, str]
    role_model_overrides: dict[str, str] = Field(default_factory=dict)
    deepseek_base_url: str = DEEPSEEK_OFFICIAL_BASE_URL

    @classmethod
    def from_settings(cls, settings: Settings) -> ModelCatalog:
        api_key_present = bool(
            settings.deepseek_api_key and settings.deepseek_api_key.get_secret_value()
        )
        current_model = settings.leader_model or settings.deepseek_pro_model
        provider = ProviderProfile(
            id=DEEPSEEK_PROVIDER_ID,
            display_name=DEEPSEEK_DISPLAY_NAME,
            configured=api_key_present,
            credential_env=DEEPSEEK_API_KEY_ENV,
            api_key_present=api_key_present,
            models=[
                ModelProfile(
                    id=settings.deepseek_pro_model,
                    display_name="DeepSeek V4 Pro",
                    capabilities=[
                        ModelCapability.STREAMING,
                        ModelCapability.TOOLS,
                        ModelCapability.REASONING,
                    ],
                    recommended_for=["leader"],
                    selected=current_model == settings.deepseek_pro_model,
                ),
                ModelProfile(
                    id=settings.deepseek_flash_model,
                    display_name="DeepSeek V4 Flash",
                    capabilities=[
                        ModelCapability.STREAMING,
                        ModelCapability.TOOLS,
                        ModelCapability.REASONING,
                    ],
                    recommended_for=["teammate", "verifier", "subagent"],
                    selected=current_model == settings.deepseek_flash_model,
                ),
            ],
        )
        return cls(
            providers_by_id={provider.id: provider},
            current=SelectedModel(
                provider_id=DEEPSEEK_PROVIDER_ID,
                model_id=current_model,
            ),
            role_models={
                "leader": settings.leader_model,
                "teammate": settings.teammate_model,
                "verifier": settings.verifier_model,
                "subagent": settings.subagent_model,
            },
            role_model_overrides=dict(settings.role_model_overrides),
            deepseek_base_url=settings.deepseek_base_url,
        )

    def providers(self) -> list[ProviderProfile]:
        return list(self.providers_by_id.values())

    def model_ids(self) -> set[str]:
        return {
            model.id
            for provider in self.providers_by_id.values()
            for model in provider.models
        }

    def require_provider(self, provider_id: str) -> ProviderProfile:
        try:
            return self.providers_by_id[provider_id]
        except KeyError as error:
            raise ModelCatalogError(
                "unsupported_provider",
                f"Unsupported provider: {provider_id}.",
                hint="Only official DeepSeek is supported by this product build.",
            ) from error

    def require_model(self, model_id: str) -> ModelProfile:
        for provider in self.providers_by_id.values():
            for model in provider.models:
                if model.id == model_id:
                    return model
        raise ModelCatalogError(
            "unsupported_model",
            f"Unsupported model: {model_id}.",
            hint="Choose one of the DeepSeek models returned by /models.",
        )

    def require_configured_provider(self, provider_id: str) -> ProviderProfile:
        provider = self.require_provider(provider_id)
        if not provider.configured:
            raise ModelCatalogError(
                "provider_not_configured",
                f"{provider.display_name} is not configured.",
                hint=f"Set {provider.credential_env}.",
            )
        return provider

    def require_supported_configuration(self) -> None:
        if self.deepseek_base_url.rstrip("/") != DEEPSEEK_OFFICIAL_BASE_URL:
            raise ModelCatalogError(
                "unsupported_provider_configuration",
                "Custom DeepSeek base URLs are not supported.",
                hint=(
                    f"Use the official DeepSeek endpoint: {DEEPSEEK_OFFICIAL_BASE_URL}."
                ),
            )

    def validate_role_models(self) -> None:
        for role, model_id in self.role_models.items():
            if model_id not in self.model_ids():
                raise ModelCatalogError(
                    "invalid_role_model",
                    f"{role} model is not in the model catalog: {model_id}.",
                    hint="Configure role models with supported DeepSeek model ids.",
                )
        for profile, model_id in self.role_model_overrides.items():
            if model_id not in self.model_ids():
                raise ModelCatalogError(
                    "invalid_role_model",
                    (
                        f"role override {profile} is not in the model catalog: "
                        f"{model_id}."
                    ),
                    hint=(
                        "Configure role model overrides with supported DeepSeek "
                        "model ids."
                    ),
                )

    def response_payload(self) -> dict[str, object]:
        return {
            "providers": [
                provider.model_dump(mode="json") for provider in self.providers()
            ],
            "current": {
                "provider_id": self.current.provider_id,
                "model_id": self.current.model_id,
            },
        }


def catalog_from_settings(settings: Settings) -> ModelCatalog:
    return ModelCatalog.from_settings(settings)
