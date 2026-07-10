from __future__ import annotations

import hashlib

from awesome_agent.context.models import (
    ContextManifestItem,
    ContextRequest,
    ContextSource,
    ContextSourceKind,
    PreparedContext,
)
from awesome_agent.context.tokens import (
    calculate_context_budget,
    estimate_messages,
)
from awesome_agent.modeling import (
    AssistantMessage,
    ModelMessage,
    SystemMessage,
    UserMessage,
)

_SOURCE_ORDER = {kind: index for index, kind in enumerate(ContextSourceKind)}
_ALWAYS_MANDATORY = frozenset(
    {
        ContextSourceKind.PRODUCT_INSTRUCTIONS,
        ContextSourceKind.WORKSPACE_INSTRUCTIONS,
        ContextSourceKind.SKILL,
        ContextSourceKind.EXPLICIT_PATH,
        ContextSourceKind.CURRENT_INPUT,
        ContextSourceKind.OPEN_TOOL_CHAIN,
    }
)


class ContextOverflow(RuntimeError):
    pass


class ContextBuilder:
    async def prepare(self, request: ContextRequest) -> PreparedContext:
        budget = calculate_context_budget(
            request.configured_total_tokens,
            request.model_context_limit,
        )
        sources = _ordered_unique(request.sources)
        mandatory_estimate = sum(
            _message_estimate(source, source.content)
            for source in sources
            if _mandatory(source)
        )
        if mandatory_estimate > budget.effective_input_limit:
            raise ContextOverflow(
                "Mandatory context exceeds the effective input limit."
            )
        optional_remaining = budget.effective_input_limit - mandatory_estimate
        messages: list[ModelMessage] = []
        manifest: list[ContextManifestItem] = []
        for source in sources:
            content = source.content
            truncated = False
            if not _mandatory(source):
                source_limit = min(
                    optional_remaining,
                    source.token_budget or optional_remaining,
                )
                content, truncated = _truncate_content(source, source_limit)
                if not content:
                    continue
            message = _message(source, content)
            estimate = estimate_messages((message,))
            if not _mandatory(source):
                if estimate > optional_remaining:
                    content, truncated_again = _truncate_content(
                        source, optional_remaining
                    )
                    truncated = truncated or truncated_again
                    if not content:
                        continue
                    message = _message(source, content)
                    estimate = estimate_messages((message,))
                optional_remaining -= estimate
            messages.append(message)
            manifest.append(
                ContextManifestItem(
                    kind=source.kind,
                    source_id=source.source_id,
                    order=len(manifest),
                    estimated_tokens=estimate,
                    truncated=truncated,
                    content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    covered_sequence_start=source.covered_sequence_start,
                    covered_sequence_end=source.covered_sequence_end,
                )
            )
        estimated = estimate_messages(tuple(messages))
        if estimated > budget.effective_input_limit:
            raise ContextOverflow("Prepared context exceeds the effective input limit.")
        return PreparedContext(
            messages=tuple(messages),
            manifest=tuple(manifest),
            estimated_input_tokens=estimated,
            effective_input_limit=budget.effective_input_limit,
            compression_recommended=estimated >= budget.compression_threshold,
        )


def _ordered_unique(sources: tuple[ContextSource, ...]) -> tuple[ContextSource, ...]:
    indexed = sorted(
        enumerate(sources),
        key=lambda item: (_SOURCE_ORDER[item[1].kind], item[0]),
    )
    seen: set[str] = set()
    result: list[ContextSource] = []
    for _, source in indexed:
        normalized = " ".join(source.content.split())
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        if digest in seen:
            continue
        seen.add(digest)
        result.append(source)
    return tuple(result)


def _mandatory(source: ContextSource) -> bool:
    return source.mandatory or source.kind in _ALWAYS_MANDATORY


def _message_estimate(source: ContextSource, content: str) -> int:
    return estimate_messages((_message(source, content),))


def _message(source: ContextSource, content: str) -> ModelMessage:
    rendered = f"[{source.kind.value}:{source.source_id}]\n{content}"
    if source.role == "system" or source.kind in {
        ContextSourceKind.PRODUCT_INSTRUCTIONS,
        ContextSourceKind.WORKSPACE_INSTRUCTIONS,
    }:
        return SystemMessage(content=rendered)
    if source.role == "assistant":
        return AssistantMessage(content=rendered)
    return UserMessage(content=rendered)


def _truncate_content(
    source: ContextSource,
    token_limit: int,
) -> tuple[str, bool]:
    if token_limit <= 0:
        return "", bool(source.content)
    if _message_estimate(source, source.content) <= token_limit:
        return source.content, False
    lines = source.content.splitlines()
    retained: list[str] = []
    for line in lines:
        candidate = "\n".join([*retained, line])
        if _message_estimate(source, candidate) > token_limit:
            break
        retained.append(line)
    if not retained and lines:
        low = 0
        high = len(lines[0])
        while low < high:
            midpoint = (low + high + 1) // 2
            if _message_estimate(source, lines[0][:midpoint]) <= token_limit:
                low = midpoint
            else:
                high = midpoint - 1
        if low:
            retained.append(lines[0][:low])
    content = "\n".join(retained)
    return content, content != source.content
