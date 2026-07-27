from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from inspect import isawaitable
from typing import Any

import openai
from openai import AsyncOpenAI
from pydantic import SecretStr

from awesome_agent.config import (
    CredentialValidation,
    CredentialValidationStatus,
    ProviderName,
)
from awesome_agent.modeling import KimiRegion
from awesome_agent.providers.deepseek import DEEPSEEK_OFFICIAL_BASE_URL
from awesome_agent.providers.kimi import KIMI_OFFICIAL_BASE_URLS

type CredentialClientFactory = Callable[..., Any]

_CREDENTIAL_CLIENT_CLOSE_TIMEOUT_SECONDS = 5.0

logger = logging.getLogger(__name__)


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
        primary_error: BaseException | None = None
        result: CredentialValidation | None = None
        try:
            await asyncio.wait_for(
                client.models.list(),
                timeout=self._timeout_seconds,
            )
        except asyncio.CancelledError as error:
            primary_error = error
        except openai.APIStatusError as error:
            if error.status_code in {401, 403}:
                result = CredentialValidation(
                    status=CredentialValidationStatus.INVALID,
                    code="credential_invalid",
                )
            else:
                result = _unverified()
        except (openai.APIConnectionError, openai.APITimeoutError, TimeoutError):
            result = _unverified()
        except Exception:
            result = _unverified()
        except BaseException as error:
            primary_error = error
        else:
            result = CredentialValidation(
                status=CredentialValidationStatus.VALID,
                code="credential_valid",
            )
        try:
            await _close_client(client)
        except asyncio.CancelledError as error:
            if primary_error is None:
                primary_error = error
        if primary_error is not None:
            raise primary_error
        assert result is not None
        return result


def _unverified() -> CredentialValidation:
    return CredentialValidation(
        status=CredentialValidationStatus.UNVERIFIED,
        code="credential_validation_unavailable",
    )


async def _close_client(client: object) -> None:
    close = getattr(client, "close", None)
    if close is None:
        close = getattr(client, "aclose", None)
    if not callable(close):
        return

    async def invoke() -> None:
        try:
            result = close()
            if isawaitable(result):
                await result
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "Credential validation client cleanup failed.",
                exc_info=True,
            )

    close_task = asyncio.create_task(
        invoke(),
        name="credential-validation-client-close",
    )
    try:
        await asyncio.wait_for(
            asyncio.shield(close_task),
            timeout=_CREDENTIAL_CLIENT_CLOSE_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        close_task.cancel()
        close_task.add_done_callback(_consume_close_result)
        logger.warning("Credential validation client cleanup timed out.")
    except asyncio.CancelledError:
        await _finish_close_after_cancellation(
            close_task,
            timeout_seconds=_CREDENTIAL_CLIENT_CLOSE_TIMEOUT_SECONDS,
        )
        if not close_task.done():
            close_task.cancel()
            close_task.add_done_callback(_consume_close_result)
            logger.warning("Credential validation client cleanup timed out.")
        raise


async def _finish_close_after_cancellation(
    close_task: asyncio.Task[None],
    *,
    timeout_seconds: float,
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while not close_task.done():
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            return
        try:
            await asyncio.wait_for(asyncio.shield(close_task), timeout=remaining)
        except asyncio.CancelledError:
            continue
        except TimeoutError:
            return


def _consume_close_result(close_task: asyncio.Task[None]) -> None:
    if close_task.cancelled():
        return
    try:
        close_task.exception()
    except Exception:
        return
