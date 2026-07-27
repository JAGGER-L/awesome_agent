from __future__ import annotations

import re
from typing import Protocol

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

from awesome_agent.core.citations import Citation
from awesome_agent.core.tools import ToolResult, ToolStatus
from awesome_agent.modeling import ModelUsage, SelectedModel


class PostAnswerDiagnostic(BaseModel):
    """Provider-neutral diagnostic emitted by optional answer finalization."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        revalidate_instances="always",
    )

    code: str = Field(pattern=r"^[a-z][a-z0-9_]{0,127}$")
    message: str = Field(min_length=1, max_length=2_000)


class PostAnswerFinalizationRequest(BaseModel):
    """Immutable facts available after the Agent has produced an answer."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        revalidate_instances="always",
    )

    user_text: str = Field(max_length=200_000)
    final_answer: str = Field(min_length=1, max_length=200_000)
    citations: tuple[Citation, ...] = Field(default=(), max_length=128)
    selected_model: SelectedModel
    remaining_model_calls: int = Field(ge=0, le=256)
    remaining_provider_retries: int = Field(ge=0, le=6)
    workspace_key: str = Field(min_length=1, max_length=128)


class PostAnswerFinalizationResult(BaseModel):
    """Bounded answer update and accounting returned by one finalizer."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        revalidate_instances="always",
    )

    final_answer: str = Field(min_length=1, max_length=200_000)
    usage: ModelUsage = Field(default_factory=ModelUsage)
    model_calls: int = Field(default=0, ge=0, le=1)
    diagnostics: tuple[PostAnswerDiagnostic, ...] = Field(
        default=(),
        max_length=32,
    )

    @field_validator("final_answer")
    @classmethod
    def validate_final_answer(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("finalizer answer must contain non-whitespace text")
        return value

    @model_validator(mode="after")
    def validate_usage_owner(self) -> PostAnswerFinalizationResult:
        if self.model_calls == 0 and self.usage != ModelUsage():
            raise ValueError("finalizer usage requires a model call")
        return self


class PostAnswerFinalizer(Protocol):
    async def finalize(
        self,
        request: PostAnswerFinalizationRequest,
    ) -> PostAnswerFinalizationResult: ...


class DisabledPostAnswerFinalizer:
    async def finalize(
        self,
        request: PostAnswerFinalizationRequest,
    ) -> PostAnswerFinalizationResult:
        return PostAnswerFinalizationResult(final_answer=request.final_answer)


class _CitationAggregationInvariantError(RuntimeError):
    pass


_MAX_TURN_CITATIONS = 128
_MAX_FINAL_ANSWER_CHARACTERS = 200_000
_CITATION_MARKER = re.compile(r"\[\[(S[0-9]+)\]\]")


def collect_tool_citations(
    tool_results: list[dict[str, JsonValue]],
) -> tuple[Citation, ...]:
    """Collect ordered Turn citations while rejecting conflicting identities."""

    ordered: list[Citation] = []
    by_id: dict[str, Citation] = {}
    by_url: dict[str, Citation] = {}
    for raw_result in tool_results:
        result = ToolResult.model_validate(raw_result)
        if result.status is not ToolStatus.SUCCESS:
            continue
        for citation in result.citations:
            previous = by_id.get(citation.id)
            if previous is None:
                same_url = by_url.get(citation.url)
                if same_url is not None and same_url.id != citation.id:
                    raise _CitationAggregationInvariantError(
                        "One source URL cannot have multiple citation IDs."
                    )
                if len(ordered) >= _MAX_TURN_CITATIONS:
                    raise _CitationAggregationInvariantError(
                        "Turn citations exceed the 128-source limit."
                    )
                by_id[citation.id] = citation
                by_url[citation.url] = citation
                ordered.append(citation)
                continue
            if previous != citation:
                raise _CitationAggregationInvariantError(
                    f"Citation {citation.id} has conflicting values."
                )
    if any(citation.id != f"S{index}" for index, citation in enumerate(ordered, 1)):
        raise _CitationAggregationInvariantError(
            "Turn citation identities must be contiguous."
        )
    return tuple(ordered)


def validate_answer_citations(
    final_answer: str,
    citations: tuple[Citation, ...],
) -> tuple[str, tuple[PostAnswerDiagnostic, ...]]:
    """Validate source markers and add a deterministic source list when absent."""

    known = {citation.id for citation in citations}
    markers = tuple(_CITATION_MARKER.findall(final_answer))
    referenced = tuple(marker for marker in markers if marker in known)
    diagnostics: list[PostAnswerDiagnostic] = []
    if any(marker not in known for marker in markers):
        diagnostics.append(
            PostAnswerDiagnostic(
                code="citation_invalid_id",
                message="The answer contains a citation ID with no matching source.",
            )
        )
    if citations and not referenced:
        final_answer, truncated = _append_sources(final_answer, citations)
        diagnostics.append(
            PostAnswerDiagnostic(
                code="citation_sources_appended",
                message="Web sources were appended because the answer cited none.",
            )
        )
        if truncated:
            diagnostics.append(
                PostAnswerDiagnostic(
                    code="citation_sources_truncated",
                    message=(
                        "The appended Web source list was bounded by the answer limit."
                    ),
                )
            )
    return final_answer, tuple(diagnostics)


def _append_sources(
    final_answer: str,
    citations: tuple[Citation, ...],
) -> tuple[str, bool]:
    answer = final_answer.rstrip()
    header = "\n\nSources:\n"
    available = _MAX_FINAL_ANSWER_CHARACTERS - len(answer) - len(header)
    if available <= 0:
        required = len(header) + len(f"- [[{citations[0].id}]]")
        answer = answer[: max(1, _MAX_FINAL_ANSWER_CHARACTERS - required)].rstrip()
        available = _MAX_FINAL_ANSWER_CHARACTERS - len(answer) - len(header)
    lines: list[str] = []
    truncated = False
    for citation in citations:
        title = _escape_source_title(citation.title)
        line = f"- [[{citation.id}]] {title} — {citation.url}\n"
        if len(line) > available:
            truncated = True
            break
        lines.append(line)
        available -= len(line)
    if not lines:
        fallback = f"- [[{citations[0].id}]]"
        lines.append(fallback[:available])
        truncated = True
    return f"{answer}{header}{''.join(lines).rstrip()}", truncated


def _escape_source_title(value: str) -> str:
    escaped = value.replace("\\", "\\\\")
    for character in "`*_[]<>#":
        escaped = escaped.replace(character, f"\\{character}")
    return escaped
