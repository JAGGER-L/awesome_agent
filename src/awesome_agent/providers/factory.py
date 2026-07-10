from __future__ import annotations

from collections.abc import Mapping

from openai import AsyncOpenAI
from pydantic import SecretStr

from awesome_agent.config.loader import SecretValues
from awesome_agent.config.models import ApplicationConfig
from awesome_agent.modeling import (
    ModelCatalog,
    ModelCatalogError,
    ModelProvider,
    ProviderId,
)
from awesome_agent.providers.deepseek import DeepSeekProvider
from awesome_agent.providers.kimi import KimiProvider


def create_provider_mapping(
    application: ApplicationConfig,
    secrets: SecretValues,
    *,
    models: Mapping[ProviderId, str] | None = None,
    timeout_seconds: float = 60.0,
    deepseek_client: AsyncOpenAI | None = None,
    kimi_client: AsyncOpenAI | None = None,
) -> dict[ProviderId, ModelProvider]:
    catalog = ModelCatalog.from_application(application)
    selections = dict(models or {})
    unknown = set(selections) - set(catalog.provider_ids())
    if unknown:
        raise ModelCatalogError(
            "unsupported_provider",
            "Provider mapping contains an unsupported Provider.",
        )
    _require_secret_status_consistency(application, secrets)
    providers: dict[ProviderId, ModelProvider] = {}
    deepseek_key = _secret_value(secrets.deepseek_api_key)
    if deepseek_key is not None:
        model = selections.get("deepseek", catalog.default_for("deepseek"))
        profile = catalog.profile(model)
        if profile.provider != "deepseek":
            raise ModelCatalogError(
                "unsupported_model",
                "DeepSeek composition requires a DeepSeek model.",
            )
        providers["deepseek"] = DeepSeekProvider(
            api_key=deepseek_key,
            model=model,
            timeout_seconds=timeout_seconds,
            client=deepseek_client,
        )
    kimi_key = _secret_value(secrets.moonshot_api_key)
    if kimi_key is not None:
        model = selections.get("kimi", catalog.default_for("kimi"))
        profile = catalog.profile(model)
        if profile.provider != "kimi":
            raise ModelCatalogError(
                "unsupported_model",
                "Kimi composition requires a Kimi model.",
            )
        providers["kimi"] = KimiProvider(
            api_key=kimi_key,
            model=model,
            region=application.providers.kimi_region,
            timeout_seconds=timeout_seconds,
            client=kimi_client,
        )
    return providers


def _require_secret_status_consistency(
    application: ApplicationConfig,
    secrets: SecretValues,
) -> None:
    expected = (
        application.secret_status.deepseek_api_key,
        application.secret_status.moonshot_api_key,
    )
    actual = (
        _secret_value(secrets.deepseek_api_key) is not None,
        _secret_value(secrets.moonshot_api_key) is not None,
    )
    if actual != expected:
        raise ModelCatalogError(
            "configuration_invalid",
            "Provider secret status does not match loaded secret values.",
        )


def _secret_value(secret: SecretStr | None) -> str | None:
    if secret is None:
        return None
    value = secret.get_secret_value()
    return value if value.strip() else None
