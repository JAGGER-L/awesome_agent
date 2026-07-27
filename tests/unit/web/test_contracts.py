from __future__ import annotations

import pytest
from pydantic import ValidationError

from awesome_agent.web import (
    MAX_WEB_FETCH_CONTENT_CHARACTERS,
    WebFetchRequest,
    WebFetchResponse,
    WebSearchRequest,
    WebSearchResponse,
    WebSearchResult,
    web_fetch_urls_equivalent,
)


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
        {"query": "\ud800"},
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

    for payload in (
        {"title": "\ud800", "url": "https://example.com", "snippet": "context"},
        {
            "title": "Example",
            "url": "https://example.com/\ud800",
            "snippet": "context",
        },
        {"title": "Example", "url": "https://example.com", "snippet": "\ud800"},
    ):
        with pytest.raises(ValidationError):
            WebSearchResult.model_validate(payload)


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


def test_fetch_request_accepts_only_public_https_documents() -> None:
    request = WebFetchRequest(url="https://example.com/article?language=en")

    assert request.url == "https://example.com/article?language=en"

    for url in (
        "http://example.com/article",
        "https://user:secret@example.com/article",
        "https://example.com/article#section",
        "https://localhost/article",
        "https://service.internal/article",
        "https://service.example/article",
        "https://printer.local/article",
        "https://host.test/article",
        "https://10.0.0.1/article",
        "https://127.0.0.1/article",
        "https://169.254.1.1/article",
        "https://192.0.2.1/article",
        "https://[::1]/article",
        "https://[fc00::1]/article",
        "https://[fe80::1]/article",
        "https://example.com/report.PDF",
        "https://example.com/report%2epDf?download=1",
        "https://example.com/report%252epdf",
        "https://example.com/report%ZZ",
        "https://example.com/article?download=%ZZ",
        "https://example.com/image.png",
        "https://example.com/archive.zip",
        "https://example.com/program.exe",
        "https://example.com/audio.mp3",
        "https://example.com/video.mp4",
        "https://example.com/document.docx",
    ):
        with pytest.raises(ValidationError):
            WebFetchRequest(url=url)

    for payload in (
        {"url": ["https://example.com"]},
        {"url": "https://example.com", "unknown": True},
    ):
        with pytest.raises(ValidationError):
            WebFetchRequest.model_validate(payload)


def test_fetch_response_is_strict_and_content_bounded() -> None:
    response = WebFetchResponse(
        url="https://example.com/article",
        content="# Article\n\nContent",
        truncated=False,
    )

    assert response.model_dump() == {
        "url": "https://example.com/article",
        "content": "# Article\n\nContent",
        "truncated": False,
    }

    for payload in (
        {
            "url": "https://example.com/article",
            "content": "x" * (MAX_WEB_FETCH_CONTENT_CHARACTERS + 1),
        },
        {"url": "https://example.com/article", "content": "   \n"},
        {"url": "https://example.com/article", "content": b"binary"},
        {"url": "https://example.com/article", "content": "\ud800"},
        {"url": "https://example.com/article", "content": "contains\x00null"},
        {
            "url": "https://example.com/article",
            "content": "content",
            "truncated": 1,
        },
        {
            "url": "https://example.com/article",
            "content": "content",
            "unknown": True,
        },
    ):
        with pytest.raises(ValidationError):
            WebFetchResponse.model_validate(payload)


def test_fetch_url_equivalence_is_conservative() -> None:
    assert web_fetch_urls_equivalent(
        "https://EXAMPLE.com:443/%7earticle?q=%41",
        "https://example.com/~article?q=A",
    )
    assert web_fetch_urls_equivalent("https://example.com", "https://example.com/")
    assert not web_fetch_urls_equivalent(
        "https://example.com/article",
        "https://example.com/other",
    )
    assert not web_fetch_urls_equivalent(
        "https://example.com/article?a=1",
        "https://example.com/article?a=2",
    )
    assert not web_fetch_urls_equivalent(
        "https://example.com/article",
        "https://127.0.0.1/article",
    )
