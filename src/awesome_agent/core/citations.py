from __future__ import annotations

from urllib.parse import urlsplit

from pydantic import AnyUrl, BaseModel, ConfigDict, Field, TypeAdapter, field_validator

_URL_ADAPTER = TypeAdapter(AnyUrl)


class Citation(BaseModel):
    """A provider-neutral source locator."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        revalidate_instances="always",
    )

    id: str = Field(pattern=r"^S[1-9][0-9]{0,5}$")
    title: str = Field(min_length=1, max_length=500)
    url: str = Field(min_length=1, max_length=8_000)

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("citation title must not be blank")
        if any(
            ord(character) < 32
            or 127 <= ord(character) <= 159
            or character in {"\u0085", "\u2028", "\u2029"}
            for character in value
        ):
            raise ValueError("citation title must be a single line")
        return value

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        if any(
            ord(character) < 32 or 127 <= ord(character) <= 159 or character.isspace()
            for character in value
        ):
            raise ValueError("citation URL contains whitespace or control characters")
        if "\\" in value:
            raise ValueError("citation URL contains an invalid path separator")
        try:
            parsed = urlsplit(value)
            port = parsed.port
            validated = _URL_ADAPTER.validate_python(value, strict=True)
        except ValueError as error:
            raise ValueError("citation URL is malformed") from error
        if parsed.scheme.lower() != "https" or not parsed.netloc:
            raise ValueError("citation URL must be an absolute HTTPS URL")
        if parsed.hostname is None:
            raise ValueError("citation URL must include a host")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("citation URL must not include user information")
        if validated.username is not None or validated.password is not None:
            raise ValueError("citation URL must not include user information")
        if port is not None and not 1 <= port <= 65_535:
            raise ValueError("citation URL contains an invalid port")
        return value


class CitationAllocator:
    """Allocate stable, URL-deduplicated source identities within one Turn."""

    def __init__(self, existing: tuple[Citation, ...] = ()) -> None:
        self._ordered: list[Citation] = []
        self._by_url: dict[str, Citation] = {}
        for index, citation in enumerate(existing, start=1):
            if citation.id != f"S{index}":
                raise ValueError("Turn citation identities must be contiguous.")
            if citation.url in self._by_url:
                raise ValueError("Turn citation URLs must be unique.")
            self._ordered.append(citation)
            self._by_url[citation.url] = citation

    def allocate(self, *, title: str, url: str) -> Citation:
        previous = self._by_url.get(url)
        if previous is not None:
            return previous
        if len(self._ordered) >= 128:
            raise ValueError("Turn citations exceed the 128-source limit.")
        citation = Citation(
            id=f"S{len(self._ordered) + 1}",
            title=title,
            url=url,
        )
        self._ordered.append(citation)
        self._by_url[citation.url] = citation
        return citation

    def snapshot(self) -> tuple[Citation, ...]:
        return tuple(self._ordered)
