from __future__ import annotations

import ipaddress
import re
from typing import Protocol, Self
from urllib.parse import unquote, urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_DOMAIN_PATTERN = re.compile(
    r"^(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)
_INVALID_PERCENT_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")
_SPECIAL_USE_HOST_SUFFIXES = (
    ".example",
    ".internal",
    ".invalid",
    ".local",
    ".localhost",
    ".localdomain",
    ".onion",
    ".test",
)
_BINARY_PATH_EXTENSIONS = (
    ".7z",
    ".aac",
    ".apk",
    ".appimage",
    ".avi",
    ".avif",
    ".bin",
    ".bmp",
    ".bz2",
    ".deb",
    ".dll",
    ".dmg",
    ".doc",
    ".docx",
    ".epub",
    ".exe",
    ".flac",
    ".flv",
    ".gif",
    ".gz",
    ".heic",
    ".heif",
    ".ico",
    ".iso",
    ".jpeg",
    ".jpg",
    ".m4a",
    ".m4v",
    ".mkv",
    ".mov",
    ".mp3",
    ".mp4",
    ".mpeg",
    ".mpg",
    ".msi",
    ".odp",
    ".ods",
    ".odt",
    ".oga",
    ".ogg",
    ".opus",
    ".pdf",
    ".pkg",
    ".png",
    ".ppt",
    ".pptx",
    ".rar",
    ".rpm",
    ".rtf",
    ".so",
    ".svg",
    ".tar",
    ".tgz",
    ".tif",
    ".tiff",
    ".wav",
    ".webm",
    ".webp",
    ".wma",
    ".wmv",
    ".xls",
    ".xlsx",
    ".xz",
    ".zip",
    ".zst",
)

MAX_WEB_FETCH_CONTENT_CHARACTERS = 24_000


class WebSearchRequest(BaseModel):
    """A bounded, provider-neutral web search request."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    query: str = Field(min_length=1, max_length=2_000)
    max_results: int = Field(default=5, ge=1, le=10)
    blocked_domains: tuple[str, ...] = Field(default=(), max_length=128)

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: str) -> str:
        _require_utf8(value, "web search query is not valid UTF-8")
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
        _require_utf8(value, "web search result title is not valid UTF-8")
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
        _require_utf8(value, "web search result URL is not valid UTF-8")
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
        _require_utf8(value, "web search result snippet is not valid UTF-8")
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


class WebFetchRequest(BaseModel):
    """A single public HTTPS document to extract through a web provider."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    url: str = Field(min_length=1, max_length=8_000)

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        return _validate_public_fetch_url(value)


class WebFetchResponse(BaseModel):
    """Provider-neutral extracted Markdown bounded for one tool result."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    url: str = Field(min_length=1, max_length=8_000)
    content: str = Field(min_length=1, max_length=MAX_WEB_FETCH_CONTENT_CHARACTERS)
    truncated: bool = False

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        return _validate_public_fetch_url(value)

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str) -> str:
        _require_utf8(value, "web fetch content is not valid UTF-8")
        if not value.strip():
            raise ValueError("web fetch content must not be blank")
        if "\x00" in value:
            raise ValueError("web fetch content contains a null byte")
        return value


class WebFetchProvider(Protocol):
    async def fetch(self, request: WebFetchRequest) -> WebFetchResponse: ...


class WebProvider(WebSearchProvider, WebFetchProvider, Protocol):
    """The complete provider-neutral Web capability."""


def web_fetch_urls_equivalent(left: str, right: str) -> bool:
    """Compare validated fetch URLs without broad redirect equivalence."""

    try:
        _validate_public_fetch_url(left)
        _validate_public_fetch_url(right)
        return _fetch_url_identity(left) == _fetch_url_identity(right)
    except ValueError:
        return False


def _validate_public_fetch_url(value: str) -> str:
    _require_utf8(value, "web fetch URL is not valid UTF-8")
    if (
        not value
        or value != value.strip()
        or "\\" in value
        or any(
            ord(character) < 32 or 127 <= ord(character) <= 159 or character.isspace()
            for character in value
        )
    ):
        raise ValueError("web fetch URL contains unsafe characters")
    if "#" in value:
        raise ValueError("web fetch URL must not include a fragment")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise ValueError("web fetch URL is malformed") from error
    if parsed.scheme.lower() != "https" or not parsed.netloc:
        raise ValueError("web fetch URL must be absolute HTTPS")
    if parsed.hostname is None:
        raise ValueError("web fetch URL must include a host")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("web fetch URL must not include user information")
    if parsed.netloc.endswith(":"):
        raise ValueError("web fetch URL contains an empty port")
    if port is not None and not 1 <= port <= 65_535:
        raise ValueError("web fetch URL contains an invalid port")

    host = parsed.hostname.lower().rstrip(".")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        try:
            ascii_host = host.encode("idna").decode("ascii")
        except UnicodeError as error:
            raise ValueError("web fetch URL host is malformed") from error
        if (
            "." not in ascii_host
            or _DOMAIN_PATTERN.fullmatch(ascii_host) is None
            or ascii_host.endswith(_SPECIAL_USE_HOST_SUFFIXES)
            or ascii_host == "home.arpa"
            or ascii_host.endswith(".home.arpa")
            or re.fullmatch(r"[0-9.]+", ascii_host) is not None
        ):
            raise ValueError("web fetch URL host is not public") from None
    else:
        if not address.is_global:
            raise ValueError("web fetch URL host is not public")

    if _INVALID_PERCENT_ESCAPE.search(parsed.query):
        raise ValueError("web fetch URL query has invalid percent encoding")
    decoded_path = _decode_path(parsed.path)
    if decoded_path.casefold().endswith(_BINARY_PATH_EXTENSIONS):
        raise ValueError("web fetch URL targets an unsupported binary resource")
    return value


def _decode_path(value: str) -> str:
    decoded = value
    for _ in range(4):
        if _INVALID_PERCENT_ESCAPE.search(decoded):
            raise ValueError("web fetch URL path has invalid percent encoding")
        try:
            candidate = unquote(decoded, encoding="utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise ValueError("web fetch URL path is not valid UTF-8") from error
        if candidate == decoded:
            return decoded
        decoded = candidate
    raise ValueError("web fetch URL path is excessively encoded")


def _require_utf8(value: str, message: str) -> None:
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as error:
        raise ValueError(message) from error


def _fetch_url_identity(value: str) -> tuple[str, int, str, str]:
    parsed = urlsplit(value)
    host = parsed.hostname
    if host is None:  # pragma: no cover - guarded by validation
        raise ValueError("web fetch URL must include a host")
    canonical_host = host.lower().rstrip(".").encode("idna").decode("ascii")
    port = parsed.port or 443
    path = _normalize_percent_encoding(parsed.path or "/")
    query = _normalize_percent_encoding(parsed.query)
    return canonical_host, port, path, query


def _normalize_percent_encoding(value: str) -> str:
    unreserved = frozenset(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
    )

    def normalize(match: re.Match[str]) -> str:
        character = chr(int(match.group(0)[1:], 16))
        return character if character in unreserved else match.group(0).upper()

    return re.sub(r"%[0-9A-Fa-f]{2}", normalize, value)


__all__ = [
    "MAX_WEB_FETCH_CONTENT_CHARACTERS",
    "WebFetchProvider",
    "WebFetchRequest",
    "WebFetchResponse",
    "WebProvider",
    "WebSearchProvider",
    "WebSearchRequest",
    "WebSearchResponse",
    "WebSearchResult",
    "web_fetch_urls_equivalent",
]
