from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import httpx
import openai
import pytest
from pydantic import SecretStr

from awesome_agent.config import CredentialValidationStatus, KimiRegion
from awesome_agent.providers import ProviderCredentialValidator


class FakeModels:
    def __init__(self, outcome: object) -> None:
        self._outcome = outcome
        self.calls = 0

    async def list(self) -> object:
        self.calls += 1
        if isinstance(self._outcome, BaseException):
            raise self._outcome
        return self._outcome


class FakeClient:
    def __init__(self, outcome: object) -> None:
        self.models = FakeModels(outcome)


def _response(status: int) -> httpx.Response:
    return httpx.Response(
        status,
        request=httpx.Request("GET", "https://provider.test/models"),
    )


def _factory(
    outcome: object,
    captured: list[dict[str, object]],
) -> Callable[..., FakeClient]:
    def create(**kwargs: object) -> FakeClient:
        captured.append(dict(kwargs))
        return FakeClient(outcome)

    return create


@pytest.mark.asyncio
async def test_validates_once_against_the_selected_provider_base_url() -> None:
    captured: list[dict[str, object]] = []
    validator = ProviderCredentialValidator(
        client_factory=_factory(object(), captured),
        timeout_seconds=3.0,
    )

    result = await validator.validate(
        "kimi",
        SecretStr("never-render-this"),
        kimi_region=KimiRegion.GLOBAL,
    )

    assert result.status is CredentialValidationStatus.VALID
    assert result.code == "credential_valid"
    assert captured == [
        {
            "api_key": "never-render-this",
            "base_url": "https://api.moonshot.ai/v1",
            "timeout": 3.0,
        }
    ]
    assert "never-render-this" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_authentication_rejection_is_invalid_without_secret_leak() -> None:
    secret = "private-invalid-secret"
    validator = ProviderCredentialValidator(
        client_factory=_factory(
            openai.AuthenticationError(
                secret,
                response=_response(401),
                body=None,
            ),
            [],
        )
    )

    result = await validator.validate(
        "deepseek",
        SecretStr(secret),
        kimi_region=KimiRegion.CN,
    )

    assert result.status is CredentialValidationStatus.INVALID
    assert result.code == "credential_invalid"
    assert secret not in repr(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        openai.APITimeoutError(
            request=httpx.Request("GET", "https://provider.test/models")
        ),
        openai.APIConnectionError(
            request=httpx.Request("GET", "https://provider.test/models")
        ),
        openai.RateLimitError("limited", response=_response(429), body=None),
        openai.InternalServerError("down", response=_response(503), body=None),
        RuntimeError("private provider detail"),
    ],
)
async def test_transient_and_unknown_failures_are_unverified(error: Exception) -> None:
    validator = ProviderCredentialValidator(client_factory=_factory(error, []))

    result = await validator.validate(
        "deepseek",
        SecretStr("secret"),
        kimi_region=KimiRegion.CN,
    )

    assert result.status is CredentialValidationStatus.UNVERIFIED
    assert result.code == "credential_validation_unavailable"
    assert "private provider detail" not in repr(result)


@pytest.mark.asyncio
async def test_cancellation_is_not_converted_to_validation_result() -> None:
    validator = ProviderCredentialValidator(
        client_factory=_factory(asyncio.CancelledError(), [])
    )

    with pytest.raises(asyncio.CancelledError):
        await validator.validate(
            "deepseek",
            SecretStr("secret"),
            kimi_region=KimiRegion.CN,
        )


def test_validator_rejects_invalid_timeout() -> None:
    with pytest.raises(ValueError, match="positive"):
        ProviderCredentialValidator(timeout_seconds=0, client_factory=Any)
