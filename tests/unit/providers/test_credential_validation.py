from __future__ import annotations

import asyncio
import logging
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
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1


def _response(status: int) -> httpx.Response:
    return httpx.Response(
        status,
        request=httpx.Request("GET", "https://provider.test/models"),
    )


def _factory(
    outcome: object,
    captured: list[dict[str, object]],
    clients: list[FakeClient] | None = None,
) -> Callable[..., FakeClient]:
    def create(**kwargs: object) -> FakeClient:
        captured.append(dict(kwargs))
        client = FakeClient(outcome)
        if clients is not None:
            clients.append(client)
        return client

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
    clients: list[FakeClient] = []
    validator = ProviderCredentialValidator(
        client_factory=_factory(asyncio.CancelledError(), [], clients)
    )

    with pytest.raises(asyncio.CancelledError):
        await validator.validate(
            "deepseek",
            SecretStr("secret"),
            kimi_region=KimiRegion.CN,
        )
    assert clients[0].close_calls == 1


@pytest.mark.asyncio
async def test_close_failure_is_logged_without_replacing_validation(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class CloseFailureClient(FakeClient):
        async def close(self) -> None:
            self.close_calls += 1
            raise RuntimeError("private close detail")

    client = CloseFailureClient(object())
    validator = ProviderCredentialValidator(client_factory=lambda **_: client)

    with caplog.at_level(logging.WARNING):
        result = await validator.validate(
            "deepseek",
            SecretStr("secret"),
            kimi_region=KimiRegion.CN,
        )

    assert result.status is CredentialValidationStatus.VALID
    assert client.close_calls == 1
    assert "Credential validation client cleanup failed" in caplog.text


@pytest.mark.asyncio
async def test_second_cancellation_during_close_preserves_primary_cancellation(
) -> None:
    class BlockingCloseClient(FakeClient):
        def __init__(self) -> None:
            super().__init__(asyncio.CancelledError("primary-cancellation"))
            self.close_entered = asyncio.Event()
            self.close_release = asyncio.Event()

        async def close(self) -> None:
            self.close_calls += 1
            self.close_entered.set()
            await self.close_release.wait()

    client = BlockingCloseClient()
    validator = ProviderCredentialValidator(client_factory=lambda **_: client)
    validating = asyncio.create_task(
        validator.validate(
            "deepseek",
            SecretStr("secret"),
            kimi_region=KimiRegion.CN,
        )
    )
    await asyncio.wait_for(client.close_entered.wait(), timeout=1)

    validating.cancel("secondary-cancellation")
    await asyncio.sleep(0)
    client.close_release.set()

    with pytest.raises(asyncio.CancelledError) as cancelled:
        await validating
    assert cancelled.value.args == ("primary-cancellation",)
    assert client.close_calls == 1


@pytest.mark.asyncio
async def test_primary_cancellation_bounds_hanging_client_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import awesome_agent.providers.credential_validation as validation_module

    class HangingCloseClient(FakeClient):
        def __init__(self) -> None:
            super().__init__(asyncio.CancelledError("primary-cancellation"))
            self.close_cancelled = False

        async def close(self) -> None:
            self.close_calls += 1
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.close_cancelled = True
                raise

    monkeypatch.setattr(
        validation_module,
        "_CREDENTIAL_CLIENT_CLOSE_TIMEOUT_SECONDS",
        0.01,
    )
    client = HangingCloseClient()
    validator = ProviderCredentialValidator(client_factory=lambda **_: client)

    with pytest.raises(asyncio.CancelledError) as cancelled:
        await asyncio.wait_for(
            validator.validate(
                "deepseek",
                SecretStr("secret"),
                kimi_region=KimiRegion.CN,
            ),
            timeout=0.5,
        )

    assert cancelled.value.args == ("primary-cancellation",)
    assert client.close_calls == 1
    await asyncio.sleep(0)
    assert client.close_cancelled is True


def test_validator_rejects_invalid_timeout() -> None:
    with pytest.raises(ValueError, match="positive"):
        ProviderCredentialValidator(timeout_seconds=0, client_factory=Any)
