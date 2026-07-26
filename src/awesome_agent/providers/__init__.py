"""Closed composition for the two supported model Providers."""

from awesome_agent.providers.credential_validation import (
    ProviderCredentialValidator,
)
from awesome_agent.providers.deepseek import (
    DEEPSEEK_OFFICIAL_BASE_URL,
    DeepSeekProvider,
)
from awesome_agent.providers.factory import (
    create_provider_mapping,
    managed_gateway_factory,
)
from awesome_agent.providers.kimi import KIMI_OFFICIAL_BASE_URLS, KimiProvider

__all__ = [
    "DEEPSEEK_OFFICIAL_BASE_URL",
    "KIMI_OFFICIAL_BASE_URLS",
    "DeepSeekProvider",
    "KimiProvider",
    "ProviderCredentialValidator",
    "create_provider_mapping",
    "managed_gateway_factory",
]
