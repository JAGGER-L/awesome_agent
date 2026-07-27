from __future__ import annotations

import pytest
from pydantic import ValidationError

from awesome_agent.web import WebSearchRequest, WebSearchResponse, WebSearchResult


def test_search_request_is_strict_bounded_and_canonicalizes_domains() -> None:
    request = WebSearchRequest(
        query="  current release  ",
        max_results=10,
        blocked_domains=("EXAMPLE.COM.",),
    )

    assert request.query == "current release"
    assert request.blocked_domains == ("example.com",)

    for payload in (
        {"query": "x", "max_results": 11},
        {"query": "x", "max_results": "5"},
        {"query": "x", "max_results": True},
        {"query": "x", "unknown": "field"},
        {"query": "   "},
        {"query": "x", "blocked_domains": ("localhost/path",)},
        {"query": "x", "blocked_domains": ("example.com", "EXAMPLE.COM")},
    ):
        with pytest.raises(ValidationError):
            WebSearchRequest.model_validate(payload)


def test_search_result_requires_a_safe_https_source() -> None:
    result = WebSearchResult(
        title="Example",
        url="https://example.com/source",
        snippet=" bounded context ",
    )

    assert result.snippet == "bounded context"

    for url in (
        "http://example.com/source",
        "https://user:secret@example.com/source",
        "https://example.com/path with space",
        "https://example.com\\source",
    ):
        with pytest.raises(ValidationError):
            WebSearchResult(title="Example", url=url, snippet="context")


def test_search_response_is_provider_neutral_and_bounded() -> None:
    result = WebSearchResult(
        title="Example",
        url="https://example.com/source",
        snippet="context",
    )
    response = WebSearchResponse(results=(result,), truncated=False)

    assert response.model_dump() == {
        "results": (
            {
                "title": "Example",
                "url": "https://example.com/source",
                "snippet": "context",
            },
        ),
        "truncated": False,
    }

    with pytest.raises(ValidationError):
        WebSearchResponse(results=(result,) * 11)
