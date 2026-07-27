from __future__ import annotations

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


def collect_tool_citations(
    tool_results: list[dict[str, JsonValue]],
) -> tuple[Citation, ...]:
    """Collect ordered Turn citations while rejecting conflicting identities."""

    ordered: list[Citation] = []
    by_id: dict[str, Citation] = {}
    for raw_result in tool_results:
        result = ToolResult.model_validate(raw_result)
        if result.status is not ToolStatus.SUCCESS:
            continue
        for citation in result.citations:
            previous = by_id.get(citation.id)
            if previous is None:
                if len(ordered) >= _MAX_TURN_CITATIONS:
                    raise _CitationAggregationInvariantError(
                        "Turn citations exceed the 128-source limit."
                    )
                by_id[citation.id] = citation
                ordered.append(citation)
                continue
            if previous != citation:
                raise _CitationAggregationInvariantError(
                    f"Citation {citation.id} has conflicting values."
                )
    return tuple(ordered)
