from __future__ import annotations

import json
import traceback
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from awesome_agent.web import (
    TavilySearchClient,
    WebProviderError,
    WebProviderErrorCode,
    WebSearchRequest,
    managed_tavily_search_client,
)
from awesome_agent.web.tavily import (
    MAX_TAVILY_RESPONSE_BYTES,
    TAVILY_SEARCH_URL,
    validate_web_proxy_url,
)

_KEY = "test-tavily-key"
_QUERY = "sensitive search query"
_PROXY = "https://proxy-secret.example:8443"


def _json_response(payload: object, *, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload)


def _client(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    proxy: bool = False,
) -> tuple[TavilySearchClient, dict[str, Any]]:
    captured: dict[str, Any] = {}

    def factory(**kwargs: Any) -> httpx.AsyncClient:
        captured.update(kwargs)
        kwargs = dict(kwargs)
        kwargs.pop("proxy", None)
        kwargs["transport"] = httpx.MockTransport(handler)
        return httpx.AsyncClient(**kwargs)

    adapter = TavilySearchClient(
        api_key=SecretStr(_KEY),
        proxy_url=SecretStr(_PROXY) if proxy else None,
        user_agent="awesome-agent/test",
        client_factory=factory,
    )
    return adapter, captured


@pytest.mark.asyncio
async def test_search_uses_fixed_bounded_basic_request_and_explicit_client() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _json_response(
            {
                "query": _QUERY,
                "results": [
                    {
                        "title": "Result A",
                        "url": "https://example.com/a",
                        "content": "Snippet A",
                        "score": 0.9,
                    },
                    {
                        "title": "Result B",
                        "url": "https://example.com/b",
                        "content": "Snippet B",
                        "score": 0.8,
                    },
                ],
                "request_id": "provider-only-id",
            }
        )

    adapter, captured = _client(handler, proxy=True)
    response = await adapter.search(
        WebSearchRequest(
            query=_QUERY,
            max_results=2,
            blocked_domains=("blocked.example",),
        )
    )
    await adapter.aclose()

    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert str(request.url) == TAVILY_SEARCH_URL
    assert request.headers["authorization"] == f"Bearer {_KEY}"
    assert request.headers["user-agent"] == "awesome-agent/test"
    assert request.headers["accept-encoding"] == "identity"
    assert json.loads(request.content) == {
        "query": _QUERY,
        "search_depth": "basic",
        "topic": "general",
        "max_results": 2,
        "include_answer": False,
        "include_raw_content": False,
        "include_images": False,
        "include_favicon": False,
        "auto_parameters": False,
        "exclude_domains": ["blocked.example"],
    }
    assert captured["trust_env"] is False
    assert captured["follow_redirects"] is False
    assert captured["proxy"] == _PROXY
    assert [item.model_dump() for item in response.results] == [
        {
            "title": "Result A",
            "url": "https://example.com/a",
            "snippet": "Snippet A",
        },
        {
            "title": "Result B",
            "url": "https://example.com/b",
            "snippet": "Snippet B",
        },
    ]
    assert response.truncated is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (400, WebProviderErrorCode.INVALID_REQUEST),
        (401, WebProviderErrorCode.AUTHENTICATION_FAILED),
        (403, WebProviderErrorCode.ACCESS_DENIED),
        (404, WebProviderErrorCode.REQUEST_REJECTED),
        (429, WebProviderErrorCode.RATE_LIMITED),
        (432, WebProviderErrorCode.USAGE_LIMIT_EXCEEDED),
        (433, WebProviderErrorCode.PAYG_LIMIT_EXCEEDED),
        (500, WebProviderErrorCode.PROVIDER_UNAVAILABLE),
        (503, WebProviderErrorCode.PROVIDER_UNAVAILABLE),
        (302, WebProviderErrorCode.MALFORMED_RESPONSE),
    ],
)
async def test_status_errors_are_stable_redacted_and_never_retried(
    status: int,
    expected: WebProviderErrorCode,
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _json_response(
            {"detail": f"{_QUERY} {_KEY} {_PROXY} {request.url}"},
            status=status,
        )

    adapter, _ = _client(handler, proxy=True)
    with pytest.raises(WebProviderError) as captured:
        await adapter.search(WebSearchRequest(query=_QUERY))
    await adapter.aclose()

    assert calls == 1
    assert captured.value.code is expected
    _assert_redacted(captured.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exception_factory", "expected"),
    [
        (
            lambda request: httpx.ReadTimeout(
                f"{_QUERY} {_KEY} {_PROXY}", request=request
            ),
            WebProviderErrorCode.TIMEOUT,
        ),
        (
            lambda request: httpx.ConnectError(
                f"{_QUERY} {_KEY} {_PROXY}", request=request
            ),
            WebProviderErrorCode.CONNECTION_FAILED,
        ),
        (
            lambda request: httpx.ReadError(
                f"{_QUERY} {_KEY} {_PROXY}", request=request
            ),
            WebProviderErrorCode.CONNECTION_FAILED,
        ),
        (
            lambda request: RuntimeError(f"{_QUERY} {_KEY} {_PROXY} {request.url}"),
            WebProviderErrorCode.PROVIDER_UNAVAILABLE,
        ),
    ],
)
async def test_transport_errors_are_stable_and_redacted(
    exception_factory: Callable[[httpx.Request], Exception],
    expected: WebProviderErrorCode,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exception_factory(request)

    adapter, _ = _client(handler, proxy=True)
    with pytest.raises(WebProviderError) as captured:
        await adapter.search(WebSearchRequest(query=_QUERY))
    await adapter.aclose()

    assert captured.value.code is expected
    _assert_redacted(captured.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="not-json", headers={"content-type": "text/plain"}),
        _json_response({"missing": []}),
        _json_response({"results": ["not-an-object"]}),
        _json_response(
            {
                "results": [
                    {"title": "Result", "url": "http://example.com", "content": "x"}
                ]
            }
        ),
        httpx.Response(
            200,
            stream=httpx.ByteStream(b"{}"),
            headers={
                "content-type": "application/json",
                "content-encoding": "gzip",
            },
        ),
        httpx.Response(
            200,
            stream=httpx.ByteStream(b"{}"),
            headers={
                "content-type": "application/json",
                "content-length": str(MAX_TAVILY_RESPONSE_BYTES + 1),
            },
        ),
        httpx.Response(
            200,
            stream=httpx.ByteStream(b"x" * (MAX_TAVILY_RESPONSE_BYTES + 1)),
            headers={"content-type": "application/json"},
        ),
    ],
)
async def test_malformed_responses_are_bounded_and_redacted(
    response: httpx.Response,
) -> None:
    adapter, _ = _client(lambda request: response)

    with pytest.raises(WebProviderError) as captured:
        await adapter.search(WebSearchRequest(query=_QUERY))
    await adapter.aclose()

    assert captured.value.code is WebProviderErrorCode.MALFORMED_RESPONSE
    _assert_redacted(captured.value)


@pytest.mark.asyncio
async def test_result_count_is_capped_even_if_provider_returns_more() -> None:
    results = [
        {
            "title": f"Result {index}",
            "url": f"https://example.com/{index}",
            "content": "context",
        }
        for index in range(10)
    ]
    adapter, _ = _client(lambda request: _json_response({"results": results}))

    response = await adapter.search(WebSearchRequest(query="query", max_results=3))
    await adapter.aclose()

    assert len(response.results) == 3
    assert response.truncated is True


def test_invalid_secrets_fail_without_echoing_values() -> None:
    with pytest.raises(ValueError) as captured:
        TavilySearchClient(api_key=SecretStr(f" {_KEY}"))
    assert _KEY not in str(captured.value)

    with pytest.raises(ValueError) as captured:
        TavilySearchClient(
            api_key=SecretStr(_KEY),
            proxy_url=SecretStr(f" {_PROXY}"),
        )
    assert _PROXY not in str(captured.value)

    def failing_factory(**kwargs: Any) -> httpx.AsyncClient:
        raise RuntimeError(f"{_KEY} {_PROXY} {kwargs!r}")

    with pytest.raises(ValueError) as captured:
        TavilySearchClient(
            api_key=SecretStr(_KEY),
            proxy_url=SecretStr(_PROXY),
            client_factory=failing_factory,
        )
    _assert_redacted(captured.value)


def test_only_transient_provider_errors_are_retryable() -> None:
    retryable = {
        WebProviderErrorCode.RATE_LIMITED,
        WebProviderErrorCode.PROVIDER_UNAVAILABLE,
        WebProviderErrorCode.TIMEOUT,
        WebProviderErrorCode.CONNECTION_FAILED,
    }

    assert {
        code for code in WebProviderErrorCode if WebProviderError(code).retryable
    } == retryable


@pytest.mark.parametrize(
    "value",
    [
        "ftp://proxy.example",
        "proxy.example:8080",
        "https://user:secret@proxy.example",
        "https://proxy.example/path with space",
        "https://proxy.example\\path",
    ],
)
def test_explicit_proxy_validation_is_side_effect_free_and_redacted(value: str) -> None:
    with pytest.raises(ValueError) as captured:
        validate_web_proxy_url(SecretStr(value))

    assert value not in str(captured.value)


def test_explicit_proxy_validation_accepts_only_absolute_http_urls() -> None:
    validate_web_proxy_url(None)
    validate_web_proxy_url(SecretStr("http://proxy.example:8080"))
    validate_web_proxy_url(SecretStr("https://proxy.example"))


@pytest.mark.asyncio
async def test_managed_client_closes_its_http_resource_exactly_once() -> None:
    transport = _CountingTransport()

    def factory(**kwargs: Any) -> httpx.AsyncClient:
        kwargs = dict(kwargs)
        kwargs.pop("proxy", None)
        kwargs["transport"] = transport
        return httpx.AsyncClient(**kwargs)

    async with managed_tavily_search_client(
        api_key=SecretStr(_KEY),
        client_factory=factory,
    ) as client:
        response = await client.search(WebSearchRequest(query="query"))
        assert response.results == ()

    assert transport.close_calls == 1
    await client.aclose()
    assert transport.close_calls == 1


def _assert_redacted(error: BaseException) -> None:
    rendered = "".join(traceback.format_exception(error))
    for secret in (_QUERY, _KEY, _PROXY, TAVILY_SEARCH_URL):
        assert secret not in rendered


class _CountingTransport(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self.close_calls = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return _json_response({"results": []})

    async def aclose(self) -> None:
        self.close_calls += 1
