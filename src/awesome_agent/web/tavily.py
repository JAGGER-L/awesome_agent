from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlsplit

import httpx
from pydantic import SecretStr, ValidationError

from awesome_agent.core.tools.web_contracts import (
    WebSearchRequest,
    WebSearchResponse,
    WebSearchResult,
)
from awesome_agent.core.tools.web_errors import WebProviderError, WebProviderErrorCode

TAVILY_SEARCH_URL = "https://api.tavily.com/search"
MAX_TAVILY_RESPONSE_BYTES = 1024 * 1024
DEFAULT_TAVILY_TIMEOUT_SECONDS = 15.0
DEFAULT_WEB_USER_AGENT = "awesome-agent"

type HttpClientFactory = Callable[..., httpx.AsyncClient]


class TavilySearchClient:
    """A narrow Tavily Search adapter with a provider-neutral result contract."""

    def __init__(
        self,
        *,
        api_key: SecretStr,
        proxy_url: SecretStr | None = None,
        timeout_seconds: float = DEFAULT_TAVILY_TIMEOUT_SECONDS,
        user_agent: str = DEFAULT_WEB_USER_AGENT,
        client: httpx.AsyncClient | None = None,
        client_factory: HttpClientFactory = httpx.AsyncClient,
    ) -> None:
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or timeout_seconds <= 0
        ):
            raise ValueError("Tavily timeout must be positive.")
        if not user_agent.strip() or len(user_agent) > 256:
            raise ValueError("Web User-Agent must be a bounded non-empty value.")
        api_key_value = api_key.get_secret_value()
        if (
            not api_key_value
            or not api_key_value.isascii()
            or api_key_value != api_key_value.strip()
            or len(api_key_value) > 1_024
            or any(
                ord(character) < 33 or ord(character) == 127
                for character in api_key_value
            )
        ):
            raise ValueError("Tavily API key is invalid.")

        proxy_value: str | None = None
        if proxy_url is not None:
            validate_web_proxy_url(proxy_url)
            proxy_value = proxy_url.get_secret_value()

        self._owns_client = client is None
        self._closed = False
        if client is not None:
            self._client = client
            return

        try:
            self._client = client_factory(
                headers={
                    "Accept": "application/json",
                    "Accept-Encoding": "identity",
                    "Authorization": f"Bearer {api_key_value}",
                    "Content-Type": "application/json",
                    "User-Agent": user_agent,
                },
                timeout=httpx.Timeout(float(timeout_seconds)),
                follow_redirects=False,
                trust_env=False,
                proxy=proxy_value,
                limits=httpx.Limits(
                    max_connections=10,
                    max_keepalive_connections=5,
                ),
            )
        except Exception:
            raise ValueError("Web client configuration is invalid.") from None

    async def search(self, request: WebSearchRequest) -> WebSearchResponse:
        payload: dict[str, object] = {
            "query": request.query,
            "search_depth": "basic",
            "topic": "general",
            "max_results": request.max_results,
            "include_answer": False,
            "include_raw_content": False,
            "include_images": False,
            "include_favicon": False,
            "auto_parameters": False,
            "exclude_domains": list(request.blocked_domains),
        }
        try:
            async with self._client.stream(
                "POST",
                TAVILY_SEARCH_URL,
                json=payload,
                follow_redirects=False,
            ) as response:
                if response.status_code != 200:
                    raise _status_error(response.status_code)
                body = await _read_bounded_json_body(response)
        except WebProviderError:
            raise
        except httpx.TimeoutException:
            raise WebProviderError(WebProviderErrorCode.TIMEOUT) from None
        except httpx.ConnectError:
            raise WebProviderError(WebProviderErrorCode.CONNECTION_FAILED) from None
        except httpx.RequestError:
            raise WebProviderError(WebProviderErrorCode.CONNECTION_FAILED) from None
        except Exception:
            raise WebProviderError(WebProviderErrorCode.PROVIDER_UNAVAILABLE) from None

        return _parse_search_response(body, requested=request.max_results)

    async def aclose(self) -> None:
        if self._closed or not self._owns_client:
            return
        self._closed = True
        try:
            await self._client.aclose()
        except WebProviderError:
            raise
        except Exception:
            raise WebProviderError(WebProviderErrorCode.CONNECTION_FAILED) from None


@asynccontextmanager
async def managed_tavily_search_client(
    *,
    api_key: SecretStr,
    proxy_url: SecretStr | None = None,
    timeout_seconds: float = DEFAULT_TAVILY_TIMEOUT_SECONDS,
    user_agent: str = DEFAULT_WEB_USER_AGENT,
    client_factory: HttpClientFactory = httpx.AsyncClient,
) -> AsyncIterator[TavilySearchClient]:
    client = TavilySearchClient(
        api_key=api_key,
        proxy_url=proxy_url,
        timeout_seconds=timeout_seconds,
        user_agent=user_agent,
        client_factory=client_factory,
    )
    try:
        yield client
    finally:
        await client.aclose()


def validate_web_proxy_url(proxy_url: SecretStr | None) -> None:
    """Validate an explicit proxy without exposing or retaining its secret value."""

    if proxy_url is None:
        return
    value = proxy_url.get_secret_value()
    if (
        not value
        or value != value.strip()
        or "\\" in value
        or any(
            ord(character) < 32 or ord(character) == 127 or character.isspace()
            for character in value
        )
    ):
        raise ValueError("Web proxy configuration is invalid.")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise ValueError("Web proxy configuration is invalid.") from None
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.netloc
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or (port is not None and not 1 <= port <= 65_535)
    ):
        raise ValueError("Web proxy configuration is invalid.")


def _status_error(status_code: int) -> WebProviderError:
    if status_code == 400:
        code = WebProviderErrorCode.INVALID_REQUEST
    elif status_code == 401:
        code = WebProviderErrorCode.AUTHENTICATION_FAILED
    elif status_code == 403:
        code = WebProviderErrorCode.ACCESS_DENIED
    elif status_code == 429:
        code = WebProviderErrorCode.RATE_LIMITED
    elif status_code == 432:
        code = WebProviderErrorCode.USAGE_LIMIT_EXCEEDED
    elif status_code == 433:
        code = WebProviderErrorCode.PAYG_LIMIT_EXCEEDED
    elif 400 <= status_code < 500:
        code = WebProviderErrorCode.REQUEST_REJECTED
    elif 500 <= status_code < 600:
        code = WebProviderErrorCode.PROVIDER_UNAVAILABLE
    else:
        code = WebProviderErrorCode.MALFORMED_RESPONSE
    return WebProviderError(code)


async def _read_bounded_json_body(response: httpx.Response) -> bytes:
    content_type = response.headers.get("content-type", "")
    if content_type.split(";", maxsplit=1)[0].strip().lower() != "application/json":
        raise WebProviderError(WebProviderErrorCode.MALFORMED_RESPONSE)
    content_encoding = response.headers.get("content-encoding", "identity").lower()
    if content_encoding not in {"", "identity"}:
        raise WebProviderError(WebProviderErrorCode.MALFORMED_RESPONSE)
    content_length = response.headers.get("content-length")
    if content_length is not None:
        try:
            declared_length = int(content_length)
        except ValueError:
            raise WebProviderError(WebProviderErrorCode.MALFORMED_RESPONSE) from None
        if declared_length < 0 or declared_length > MAX_TAVILY_RESPONSE_BYTES:
            raise WebProviderError(WebProviderErrorCode.MALFORMED_RESPONSE)

    body = bytearray()
    async for chunk in response.aiter_bytes():
        if len(body) + len(chunk) > MAX_TAVILY_RESPONSE_BYTES:
            raise WebProviderError(WebProviderErrorCode.MALFORMED_RESPONSE)
        body.extend(chunk)
    return bytes(body)


def _parse_search_response(body: bytes, *, requested: int) -> WebSearchResponse:
    try:
        decoded = body.decode("utf-8", errors="strict")
        payload: Any = json.loads(decoded)
        if not isinstance(payload, dict):
            raise TypeError
        raw_results = payload.get("results")
        if not isinstance(raw_results, list):
            raise TypeError

        results: list[WebSearchResult] = []
        for raw in raw_results[:requested]:
            if not isinstance(raw, dict):
                raise TypeError
            title = raw.get("title")
            url = raw.get("url")
            content = raw.get("content")
            if not isinstance(title, str) or not isinstance(url, str):
                raise TypeError
            if not isinstance(content, str):
                raise TypeError
            results.append(
                WebSearchResult(
                    title=title,
                    url=url,
                    snippet=content,
                )
            )
        return WebSearchResponse(
            results=tuple(results),
            truncated=len(raw_results) > requested,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValidationError):
        raise WebProviderError(WebProviderErrorCode.MALFORMED_RESPONSE) from None


__all__ = [
    "DEFAULT_TAVILY_TIMEOUT_SECONDS",
    "MAX_TAVILY_RESPONSE_BYTES",
    "TAVILY_SEARCH_URL",
    "TavilySearchClient",
    "managed_tavily_search_client",
    "validate_web_proxy_url",
]
