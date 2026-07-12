from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import openai
from openai import AsyncOpenAI
from pydantic import SecretStr

from awesome_agent.config import (
    CredentialValidation,
    CredentialValidationStatus,
    KimiRegion,
    ProviderName,
)
from awesome_agent.providers.deepseek import DEEPSEEK_OFFICIAL_BASE_URL
from awesome_agent.providers.kimi import KIMI_OFFICIAL_BASE_URLS

type CredentialClientFactory = Callable[..., Any]


class ProviderCredentialValidator:
    def __init__(
        self,
        *,
        timeout_seconds: float = 10.0,
        client_factory: CredentialClientFactory | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("Credential validation timeout must be positive.")
        self._timeout_seconds = timeout_seconds
        self._client_factory = client_factory or AsyncOpenAI

    async def validate(
        self,
        provider: ProviderName,
        api_key: SecretStr,
        *,
        kimi_region: KimiRegion,
    ) -> CredentialValidation:
        client = self._client_factory(
            api_key=api_key.get_secret_value(),
            base_url=(
                DEEPSEEK_OFFICIAL_BASE_URL
                if provider == "deepseek"
                else KIMI_OFFICIAL_BASE_URLS[kimi_region]
            ),
            timeout=self._timeout_seconds,
        )
        try:
            await asyncio.wait_for(
                client.models.list(),
                timeout=self._timeout_seconds,
            )
        except asyncio.CancelledError:
            raise
        except openai.APIStatusError as error:
            if error.status_code in {401, 403}:
                return CredentialValidation(
                    status=CredentialValidationStatus.INVALID,
                    code="credential_invalid",
                )
            return _unverified()
        except (openai.APIConnectionError, openai.APITimeoutError, TimeoutError):
            return _unverified()
        except Exception:
            return _unverified()
        return CredentialValidation(
            status=CredentialValidationStatus.VALID,
            code="credential_valid",
        )


def _unverified() -> CredentialValidation:
    return CredentialValidation(
        status=CredentialValidationStatus.UNVERIFIED,
        code="credential_validation_unavailable",
    )
