import pytest

from awesome_agent.context import (
    ContextBuilder,
    ContextOverflow,
    ContextRequest,
    ContextSource,
    ContextSourceKind,
)


def _source(
    kind: ContextSourceKind,
    content: str,
    *,
    mandatory: bool = False,
    source_id: str | None = None,
    token_budget: int | None = None,
) -> ContextSource:
    return ContextSource(
        kind=kind,
        source_id=source_id or kind.value,
        content=content,
        mandatory=mandatory,
        token_budget=token_budget,
    )


@pytest.mark.asyncio
async def test_builder_uses_stable_source_order_independent_of_input_order() -> None:
    sources = tuple(
        _source(kind, kind.value, mandatory=kind is ContextSourceKind.CURRENT_INPUT)
        for kind in reversed(tuple(ContextSourceKind))
    )

    prepared = await ContextBuilder().prepare(
        ContextRequest(
            sources=sources,
            configured_total_tokens=262_144,
            model_context_limit=262_144,
        )
    )

    assert [item.kind for item in prepared.manifest] == list(ContextSourceKind)
    assert [item.order for item in prepared.manifest] == list(
        range(len(ContextSourceKind))
    )
    assert all(len(item.content_hash) == 64 for item in prepared.manifest)


@pytest.mark.asyncio
async def test_exact_normalized_duplicate_keeps_higher_priority_label() -> None:
    prepared = await ContextBuilder().prepare(
        ContextRequest(
            sources=(
                _source(ContextSourceKind.USER_MEMORY, "same  fact"),
                _source(ContextSourceKind.MEM0, " same fact \n"),
                _source(ContextSourceKind.WORKSPACE_MEMORY, "conflicting fact"),
                _source(ContextSourceKind.CURRENT_INPUT, "question", mandatory=True),
            ),
            configured_total_tokens=262_144,
            model_context_limit=262_144,
        )
    )

    assert [item.kind for item in prepared.manifest] == [
        ContextSourceKind.USER_MEMORY,
        ContextSourceKind.WORKSPACE_MEMORY,
        ContextSourceKind.CURRENT_INPUT,
    ]


@pytest.mark.asyncio
async def test_optional_sources_truncate_but_mandatory_sources_are_preserved() -> None:
    prepared = await ContextBuilder().prepare(
        ContextRequest(
            sources=(
                _source(
                    ContextSourceKind.THREAD_SUMMARY,
                    "\n".join(f"summary {index}" for index in range(500)),
                    token_budget=100,
                ),
                _source(
                    ContextSourceKind.CURRENT_INPUT,
                    "must remain exact",
                    mandatory=True,
                ),
            ),
            configured_total_tokens=40_000,
            model_context_limit=40_000,
        )
    )

    assert prepared.manifest[0].truncated is True
    assert prepared.messages[-1].content.endswith("must remain exact")


@pytest.mark.asyncio
async def test_mandatory_overflow_is_explicit() -> None:
    with pytest.raises(ContextOverflow):
        await ContextBuilder().prepare(
            ContextRequest(
                sources=(
                    _source(
                        ContextSourceKind.CURRENT_INPUT,
                        "x" * 100_000,
                        mandatory=True,
                    ),
                ),
                configured_total_tokens=40_000,
                model_context_limit=40_000,
            )
        )


@pytest.mark.asyncio
async def test_manifest_contains_coverage_but_not_source_body() -> None:
    source = ContextSource(
        kind=ContextSourceKind.RECENT_TURNS,
        source_id="turns:1-4",
        content="private source body",
        covered_sequence_start=3,
        covered_sequence_end=10,
    )
    prepared = await ContextBuilder().prepare(
        ContextRequest(
            sources=(source,),
            configured_total_tokens=262_144,
            model_context_limit=262_144,
        )
    )

    item = prepared.manifest[0]
    assert (item.covered_sequence_start, item.covered_sequence_end) == (3, 10)
    assert "private source body" not in item.model_dump_json()
