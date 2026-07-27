from __future__ import annotations

import re
from typing import Protocol, Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_DOMAIN_PATTERN = re.compile(
    r"^(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)


class WebSearchRequest(BaseModel):
    """A bounded, provider-neutral web search request."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    query: str = Field(min_length=1, max_length=2_000)
    max_results: int = Field(default=5, ge=1, le=10)
    blocked_domains: tuple[str, ...] = Field(default=(), max_length=128)

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: str) -> str:
        query = value.strip()
        if not query:
            raise ValueError("web search query must not be blank")
        if any(
            character == "\x00"
            or 127 <= ord(character) <= 159
            or character in {"\u0085", "\u2028", "\u2029"}
            for character in query
        ):
            raise ValueError("web search query contains unsupported control characters")
        return query

    @field_validator("blocked_domains")
    @classmethod
    def validate_blocked_domains(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized: list[str] = []
        for domain in value:
            candidate = domain.strip().lower().rstrip(".")
            if (
                not candidate
                or not candidate.isascii()
                or _DOMAIN_PATTERN.fullmatch(candidate) is None
            ):
                raise ValueError("blocked domain must be a canonical ASCII hostname")
            normalized.append(candidate)
        if len(set(normalized)) != len(normalized):
            raise ValueError("blocked domains must be unique")
        return tuple(normalized)


class WebSearchResult(BaseModel):
    """One source returned by any supported web search provider."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    title: str = Field(min_length=1, max_length=500)
    url: str = Field(min_length=1, max_length=8_000)
    snippet: str = Field(max_length=4_000)

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        title = value.strip()
        if not title:
            raise ValueError("web search result title must not be blank")
        if any(
            ord(character) < 32
            or 127 <= ord(character) <= 159
            or character in {"\u0085", "\u2028", "\u2029"}
            for character in title
        ):
            raise ValueError("web search result title must be a single line")
        return title

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        if any(
            ord(character) < 32 or 127 <= ord(character) <= 159 or character.isspace()
            for character in value
        ):
            raise ValueError("web search result URL contains unsafe characters")
        if "\\" in value:
            raise ValueError("web search result URL contains an invalid separator")
        try:
            parsed = urlsplit(value)
            port = parsed.port
        except ValueError as error:
            raise ValueError("web search result URL is malformed") from error
        if parsed.scheme.lower() != "https" or not parsed.netloc:
            raise ValueError("web search result URL must be absolute HTTPS")
        if parsed.hostname is None:
            raise ValueError("web search result URL must include a host")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("web search result URL must not include user information")
        if port is not None and not 1 <= port <= 65_535:
            raise ValueError("web search result URL contains an invalid port")
        return value

    @field_validator("snippet")
    @classmethod
    def validate_snippet(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("web search result snippet contains a null byte")
        return value.strip()


class WebSearchResponse(BaseModel):
    """A bounded search response with no provider-specific wire fields."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    results: tuple[WebSearchResult, ...] = Field(max_length=10)
    truncated: bool = False

    @model_validator(mode="after")
    def validate_truncation(self) -> Self:
        if len(self.results) > 10:
            raise ValueError("web search response exceeds the result limit")
        return self


class WebSearchProvider(Protocol):
    async def search(self, request: WebSearchRequest) -> WebSearchResponse: ...


__all__ = [
    "WebSearchProvider",
    "WebSearchRequest",
    "WebSearchResponse",
    "WebSearchResult",
]
