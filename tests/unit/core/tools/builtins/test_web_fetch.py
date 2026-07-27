from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import cast

import pytest
from pydantic import ValidationError

from awesome_agent.core.citations import CitationAllocator
from awesome_agent.core.tools.builtins.web_fetch import (
    MAX_WEB_FETCH_CONTENT_CHARS,
    MAX_WEB_FETCH_OUTPUT_CHARS,
    WebFetchArguments,
    create_web_fetch_registration,
)
from awesome_agent.core.tools.context import CapabilityQuotaLedger, ToolExecutionContext
from awesome_agent.core.tools.contracts import ToolErrorCode
from awesome_agent.core.tools.errors import ExpectedToolFailure
from awesome_agent.core.tools.registry import ToolReplaySafety
from awesome_agent.web import (
    WebFetchRequest,
    WebFetchResponse,
    WebProviderError,
    WebProviderErrorCode,
    WebSearchRequest,
    WebSearchResponse,
)


class StubWebProvider:
    def __init__(
        self,
        response: WebFetchResponse | None = None,
        error: WebProviderError | None = None,
    ) -> None:
        self.response = response or WebFetchResponse(
            url="https://example.com/page",
            content="content",
        )
        self.error = error
        self.requests: list[WebFetchRequest] = []

    async def search(self, request: WebSearchRequest) -> WebSearchResponse:
        del request
        return WebSearchResponse(results=())

    async def fetch(self, request: WebFetchRequest) -> WebFetchResponse:
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


def test_registration_is_strict_network_read_and_non_replayable() -> None:
    registration = create_web_fetch_registration(StubWebProvider())

    assert registration.spec.name == "web_fetch"
    assert registration.spec.capability == "network.read"
    assert registration.spec.read_only is True
    assert registration.replay_safety is ToolReplaySafety.NON_REPLAYABLE
    assert registration.timeout_resolver is not None
    assert (
        registration.timeout_resolver(
            WebFetchArguments(url="https://example.com/page")
        )
        == 20.0
    )
    assert registration.spec.input_schema["additionalProperties"] is False
    properties = registration.spec.input_schema["properties"]
    assert isinstance(properties, dict)
    assert set(properties) == {"url"}

    for payload in (
        {"url": "https://example.com", "unknown": True},
        {"url": 1},
        {"url": "http://example.com"},
        {"url": "https://127.0.0.1/private"},
        {"url": "https://user@example.com/private"},
    ):
        with pytest.raises(ValidationError):
            WebFetchArguments.model_validate(payload)


@pytest.mark.asyncio
async def test_handler_returns_bounded_content_and_one_preserved_citation() -> None:
    body = "文" * MAX_WEB_FETCH_CONTENT_CHARS
    provider = StubWebProvider(
        WebFetchResponse(
            url="https://example.com/~user?x=A",
            content=body,
            truncated=True,
        )
    )
    registration = create_web_fetch_registration(provider)
    requested_url = "https://EXAMPLE.com:443/%7euser?x=%41"
    arguments = WebFetchArguments(url=requested_url)
    context = _context()

    registration.admit(arguments, context)
    output = await registration.handler(arguments, context)

    assert provider.requests == [WebFetchRequest(url=requested_url)]
    rendered = json.loads(output.content)
    assert rendered == {
        "source_id": "S1",
        "url": requested_url,
        "content": "文" * MAX_WEB_FETCH_CONTENT_CHARS,
        "truncated": True,
    }
    assert len(rendered["content"]) == MAX_WEB_FETCH_CONTENT_CHARS
    assert output.metadata == {
        "content_chars": MAX_WEB_FETCH_CONTENT_CHARS,
        "truncated": True,
    }
    assert [citation.model_dump() for citation in output.citations] == [
        {
            "id": "S1",
            "title": "Fetched content from example.com",
            "url": requested_url,
        }
    ]
    assert context.capability_quotas.used("network.read") == 1
    assert output.presentation is not None
    assert output.presentation.target == "Web"
    assert output.presentation.summary == "24000 characters"
    assert output.presentation.detail is None
    assert "example.com" not in output.presentation.model_dump_json()
    assert "文" not in output.presentation.model_dump_json()

    description = registration.describe(arguments)
    assert description.approval_operation == "send a requested URL to Tavily"
    assert "Tavily Privacy Policy" in description.approval_target
    assert "Platform Terms" in description.approval_target
    assert "https://www.tavily.com/privacy" in description.approval_target
    assert "https://www.tavily.com/terms" in description.approval_target
    assert "example.com" not in description.model_dump_json()


@pytest.mark.asyncio
async def test_handler_rejects_unrelated_provider_url_before_citation() -> None:
    provider = StubWebProvider(
        WebFetchResponse(
            url="https://example.com/unrelated",
            content="content",
        )
    )
    registration = create_web_fetch_registration(provider)
    context = _context()

    with pytest.raises(ExpectedToolFailure) as captured:
        await registration.handler(
            WebFetchArguments(url="https://example.com/requested"),
            context,
        )

    assert captured.value.code is ToolErrorCode.WEB_MALFORMED_RESPONSE
    assert "requested" not in captured.value.message
    assert "unrelated" not in captured.value.message
    assert context.capability_quotas.used("network.read") == 1
    assert context.citation_allocator.allocate(
        title="subsequent citation",
        url="https://example.com/subsequent",
    ).id == "S1"


@pytest.mark.asyncio
async def test_handler_keeps_escaped_json_within_executor_limit() -> None:
    body = ('"\\\n\r\t\b\f\x01' * MAX_WEB_FETCH_CONTENT_CHARS)[
        :MAX_WEB_FETCH_CONTENT_CHARS
    ]
    requested_url = "https://example.com/page"
    provider = StubWebProvider(
        WebFetchResponse(url=requested_url, content=body)
    )
    registration = create_web_fetch_registration(provider)

    output = await registration.handler(
        WebFetchArguments(url=requested_url),
        _context(),
    )

    rendered = json.loads(output.content)
    assert len(output.content) <= MAX_WEB_FETCH_OUTPUT_CHARS
    assert rendered["content"] == body[: len(rendered["content"])]
    assert len(rendered["content"]) < MAX_WEB_FETCH_CONTENT_CHARS
    assert rendered["truncated"] is True
    assert output.metadata == {
        "content_chars": len(rendered["content"]),
        "truncated": True,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "https://blocked.example.com/page",
        "https://child.blocked.example.com/page",
    ],
)
async def test_blocked_domain_and_subdomains_are_hard_rejected(url: str) -> None:
    provider = StubWebProvider()
    registration = create_web_fetch_registration(
        provider,
        blocked_domains=("BLOCKED.EXAMPLE.COM.",),
    )
    arguments = WebFetchArguments(url=url)
    context = _context()

    with pytest.raises(ExpectedToolFailure) as admitted:
        registration.admit(arguments, context)
    with pytest.raises(ExpectedToolFailure) as invoked:
        await registration.handler(arguments, context)

    assert admitted.value.code is ToolErrorCode.WEB_REQUEST_REJECTED
    assert invoked.value.code is ToolErrorCode.WEB_REQUEST_REJECTED
    assert url not in admitted.value.message
    assert context.capability_quotas.used("network.read") == 0
    assert provider.requests == []


def test_blocked_domain_rejects_idna_equivalent_unicode_host() -> None:
    provider = StubWebProvider()
    registration = create_web_fetch_registration(
        provider,
        blocked_domains=("xn--fsqu00a.xn--55qx5d.cn",),
    )
    arguments = WebFetchArguments(url="https://例子.公司.cn/page")
    context = _context()

    with pytest.raises(ExpectedToolFailure) as captured:
        registration.admit(arguments, context)

    assert captured.value.code is ToolErrorCode.WEB_REQUEST_REJECTED
    assert "例子" not in captured.value.message
    assert context.capability_quotas.used("network.read") == 0
    assert provider.requests == []


@pytest.mark.asyncio
async def test_similar_domain_is_not_blocked_and_consumes_after_admission() -> None:
    requested_url = "https://notblocked.example.com/page"
    provider = StubWebProvider(
        WebFetchResponse(url=requested_url, content="content")
    )
    registration = create_web_fetch_registration(
        provider,
        blocked_domains=("blocked.example.com",),
    )
    arguments = WebFetchArguments(url=requested_url)
    context = _context()

    registration.admit(arguments, context)
    assert context.capability_quotas.used("network.read") == 0
    await registration.handler(arguments, context)

    assert context.capability_quotas.used("network.read") == 1
    assert provider.requests == [
        WebFetchRequest(url="https://notblocked.example.com/page")
    ]


@pytest.mark.asyncio
async def test_provider_error_uses_shared_redacted_tool_mapping() -> None:
    requested_url = "https://example.com/private-path"
    provider = StubWebProvider(
        error=WebProviderError(WebProviderErrorCode.AUTHENTICATION_FAILED)
    )
    registration = create_web_fetch_registration(provider)

    with pytest.raises(ExpectedToolFailure) as captured:
        await registration.handler(WebFetchArguments(url=requested_url), _context())

    assert captured.value.code is ToolErrorCode.WEB_CREDENTIAL_REJECTED
    assert captured.value.retryable is False
    assert requested_url not in captured.value.message


def test_admission_rejects_exhausted_budget_without_consuming() -> None:
    provider = StubWebProvider()
    registration = create_web_fetch_registration(provider)
    arguments = WebFetchArguments(url="https://example.com/page")
    context = _context(limit=0)

    with pytest.raises(ExpectedToolFailure) as captured:
        registration.admit(arguments, context)

    assert captured.value.code is ToolErrorCode.WEB_REQUEST_BUDGET_EXHAUSTED
    assert context.capability_quotas.used("network.read") == 0
    assert provider.requests == []
