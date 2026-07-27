from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import cast

import pytest
from pydantic import ValidationError

from awesome_agent.core.citations import CitationAllocator
from awesome_agent.core.tools.builtins.web_search import (
    MAX_WEB_SEARCH_OUTPUT_CHARS,
    WebSearchArguments,
    create_web_search_registration,
)
from awesome_agent.core.tools.context import CapabilityQuotaLedger, ToolExecutionContext
from awesome_agent.core.tools.contracts import ToolErrorCode
from awesome_agent.core.tools.errors import ExpectedToolFailure
from awesome_agent.core.tools.registry import ToolReplaySafety
from awesome_agent.web import (
    WebProviderError,
    WebProviderErrorCode,
    WebSearchRequest,
    WebSearchResponse,
    WebSearchResult,
)


class StubSearchProvider:
    def __init__(
        self,
        response: WebSearchResponse | None = None,
        error: WebProviderError | None = None,
    ) -> None:
        self.response = response or WebSearchResponse(results=())
        self.error = error
        self.requests: list[WebSearchRequest] = []

    async def search(self, request: WebSearchRequest) -> WebSearchResponse:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return self.response


@dataclass
class _TestContext:
    capability_quotas: CapabilityQuotaLedger = field(
        default_factory=lambda: CapabilityQuotaLedger({"network.read": 8})
    )
    citation_allocator: CitationAllocator = field(default_factory=CitationAllocator)


def _context(*, limit: int = 8) -> ToolExecutionContext:
    return cast(
        ToolExecutionContext,
        _TestContext(capability_quotas=CapabilityQuotaLedger({"network.read": limit})),
    )


def _result(index: int, *, snippet: str = "context") -> WebSearchResult:
    return WebSearchResult(
        title=f"Result {index}",
        url=f"https://example.com/{index}",
        snippet=snippet,
    )


def test_registration_is_strict_network_read_and_non_replayable() -> None:
    registration = create_web_search_registration(StubSearchProvider())

    assert registration.spec.name == "web_search"
    assert registration.spec.capability == "network.read"
    assert registration.spec.read_only is True
    assert registration.replay_safety is ToolReplaySafety.NON_REPLAYABLE
    assert registration.timeout_resolver is not None
    assert registration.timeout_resolver(WebSearchArguments(query="query")) == 20.0
    assert registration.spec.input_schema["additionalProperties"] is False

    for payload in (
        {"query": "query", "unknown": True},
        {"query": "query", "max_results": "5"},
        {"query": "query", "max_results": 0},
        {"query": "   "},
    ):
        with pytest.raises(ValidationError):
            WebSearchArguments.model_validate(payload)


@pytest.mark.asyncio
async def test_handler_returns_bounded_structured_provider_neutral_output() -> None:
    provider = StubSearchProvider(WebSearchResponse(results=(_result(1), _result(2))))
    registration = create_web_search_registration(
        provider,
        blocked_domains=("BLOCKED.EXAMPLE.",),
    )
    arguments = WebSearchArguments(query="  current\nrelease  ", max_results=2)
    context = _context()

    registration.admit(arguments, context)
    output = await registration.handler(arguments, context)

    assert provider.requests == [
        WebSearchRequest(
            query="current\nrelease",
            max_results=2,
            blocked_domains=("blocked.example",),
        )
    ]
    assert json.loads(output.content) == {
        "results": [
            {
                "source_id": "S1",
                "title": "Result 1",
                "url": "https://example.com/1",
                "snippet": "context",
            },
            {
                "source_id": "S2",
                "title": "Result 2",
                "url": "https://example.com/2",
                "snippet": "context",
            },
        ]
    }
    assert output.metadata == {
        "result_count": 2,
        "truncated": False,
    }
    assert [citation.model_dump() for citation in output.citations] == [
        {
            "id": "S1",
            "title": "Result 1",
            "url": "https://example.com/1",
        },
        {
            "id": "S2",
            "title": "Result 2",
            "url": "https://example.com/2",
        },
    ]
    assert context.capability_quotas.used("network.read") == 1
    assert output.presentation is not None
    assert output.presentation.target == "Web"
    assert output.presentation.summary == "2 results"
    assert output.presentation.detail is None

    description = registration.describe(arguments)
    assert description.approval_operation == "send a search query to Tavily"
    assert "Tavily Privacy Policy" in description.approval_target
    assert "Platform Terms" in description.approval_target
    assert "current" not in description.approval_target


@pytest.mark.asyncio
async def test_handler_truncates_at_the_tool_output_boundary() -> None:
    provider = StubSearchProvider(
        WebSearchResponse(
            results=tuple(_result(index, snippet="x" * 4_000) for index in range(10))
        )
    )
    registration = create_web_search_registration(provider)

    output = await registration.handler(WebSearchArguments(query="query"), _context())

    assert len(output.content) <= MAX_WEB_SEARCH_OUTPUT_CHARS
    assert output.metadata["truncated"] is True
    assert 0 < cast(int, output.metadata["result_count"]) < 10


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("web_code", "tool_code", "retryable"),
    [
        (
            WebProviderErrorCode.INVALID_REQUEST,
            ToolErrorCode("web_request_rejected"),
            False,
        ),
        (
            WebProviderErrorCode.REQUEST_REJECTED,
            ToolErrorCode("web_request_rejected"),
            False,
        ),
        (
            WebProviderErrorCode.AUTHENTICATION_FAILED,
            ToolErrorCode("web_credential_rejected"),
            False,
        ),
        (
            WebProviderErrorCode.ACCESS_DENIED,
            ToolErrorCode("web_credential_rejected"),
            False,
        ),
        (
            WebProviderErrorCode.USAGE_LIMIT_EXCEEDED,
            ToolErrorCode("web_quota_exhausted"),
            False,
        ),
        (
            WebProviderErrorCode.PAYG_LIMIT_EXCEEDED,
            ToolErrorCode("web_quota_exhausted"),
            False,
        ),
        (
            WebProviderErrorCode.PROVIDER_UNAVAILABLE,
            ToolErrorCode("web_provider_unavailable"),
            True,
        ),
        (
            WebProviderErrorCode.CONNECTION_FAILED,
            ToolErrorCode("web_connection_failed"),
            True,
        ),
        (WebProviderErrorCode.TIMEOUT, ToolErrorCode("web_timeout"), True),
        (
            WebProviderErrorCode.MALFORMED_RESPONSE,
            ToolErrorCode("web_malformed_response"),
            False,
        ),
        (
            WebProviderErrorCode.RATE_LIMITED,
            ToolErrorCode("web_rate_limited"),
            True,
        ),
    ],
)
async def test_handler_maps_provider_errors_to_stable_tool_errors(
    web_code: WebProviderErrorCode,
    tool_code: ToolErrorCode,
    retryable: bool,
) -> None:
    provider = StubSearchProvider(error=WebProviderError(web_code))
    registration = create_web_search_registration(provider)

    with pytest.raises(ExpectedToolFailure) as captured:
        await registration.handler(WebSearchArguments(query="secret query"), _context())

    assert captured.value.code is tool_code
    assert captured.value.metadata == {}
    assert captured.value.retryable is retryable
    assert "secret query" not in captured.value.message


def test_admission_rejects_an_exhausted_budget_without_consuming() -> None:
    provider = StubSearchProvider()
    registration = create_web_search_registration(provider)
    arguments = WebSearchArguments(query="query")
    context = _context(limit=0)

    with pytest.raises(ExpectedToolFailure) as captured:
        registration.admit(arguments, context)

    assert captured.value.code is ToolErrorCode.WEB_REQUEST_BUDGET_EXHAUSTED
    assert context.capability_quotas.used("network.read") == 0
    assert provider.requests == []
