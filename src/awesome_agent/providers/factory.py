from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import AsyncExitStack, asynccontextmanager

from openai import AsyncOpenAI
from pydantic import SecretStr

from awesome_agent.config.loader import SecretValues
from awesome_agent.config.models import ApplicationConfig
from awesome_agent.modeling import (
    GatewayFactory,
    ModelCatalog,
    ModelCatalogError,
    ModelGateway,
    ModelProvider,
    ProviderId,
    RetryPolicy,
)
from awesome_agent.providers.deepseek import (
    DEEPSEEK_OFFICIAL_BASE_URL,
    DeepSeekProvider,
)
from awesome_agent.providers.kimi import KIMI_OFFICIAL_BASE_URLS, KimiProvider

type ProviderClientFactory = Callable[..., AsyncOpenAI]

logger = logging.getLogger(__name__)


@asynccontextmanager
async def managed_gateway_factory(
    application: ApplicationConfig,
    secrets: SecretValues,
    *,
    timeout_seconds: float = 60.0,
    client_factory: ProviderClientFactory | None = None,
) -> AsyncIterator[GatewayFactory]:
    """Create one candidate-bound pool of reusable provider HTTP clients."""

    if timeout_seconds <= 0:
        raise ValueError("Provider timeout must be positive.")
    _require_secret_status_consistency(application, secrets)
    catalog = ModelCatalog.from_application(application)
    construct_client = client_factory or AsyncOpenAI
    clients: dict[ProviderId, AsyncOpenAI] = {}
    resources = AsyncExitStack()
    try:
        deepseek_key = _secret_value(secrets.deepseek_api_key)
        if deepseek_key is not None:
            deepseek_client = construct_client(
                api_key=deepseek_key,
                base_url=DEEPSEEK_OFFICIAL_BASE_URL,
                timeout=timeout_seconds,
            )
            resources.push_async_callback(deepseek_client.close)
            clients["deepseek"] = deepseek_client
        kimi_key = _secret_value(secrets.moonshot_api_key)
        if kimi_key is not None:
            kimi_client = construct_client(
                api_key=kimi_key,
                base_url=KIMI_OFFICIAL_BASE_URLS[application.providers.kimi_region],
                timeout=timeout_seconds,
            )
            resources.push_async_callback(kimi_client.close)
            clients["kimi"] = kimi_client

        def build(provider: ProviderId, model: str) -> ModelGateway:
            profile = catalog.profile(model)
            if profile.provider != provider:
                raise ModelCatalogError(
                    "unsupported_model",
                    "Model selection does not belong to the requested Provider.",
                )
            client = clients.get(provider)
            if client is None:
                raise AssertionError(f"{provider} credential preflight was bypassed.")
            if provider == "deepseek":
                adapter: ModelProvider = DeepSeekProvider(
                    api_key=deepseek_key or "",
                    model=model,
                    timeout_seconds=timeout_seconds,
                    client=client,
                )
            else:
                adapter = KimiProvider(
                    api_key=kimi_key or "",
                    model=model,
                    region=application.providers.kimi_region,
                    timeout_seconds=timeout_seconds,
                    client=client,
                )
            return ModelGateway(
                {provider: adapter},
                retry_policy=RetryPolicy(
                    max_retries=application.budgets.provider_retries
                ),
                sleeper=asyncio.sleep,
            )

    except BaseException:
        await _close_provider_resources_preserving_primary(resources)
        raise

    try:
        yield build
    except BaseException:
        await _close_provider_resources_preserving_primary(resources)
        raise
    else:
        await resources.aclose()


async def _close_provider_resources_preserving_primary(
    resources: AsyncExitStack,
) -> None:
    try:
        await resources.aclose()
    except BaseException:
        logger.warning(
            "Provider client cleanup failed while preserving the primary failure."
        )


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
