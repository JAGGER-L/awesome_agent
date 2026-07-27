import pytest

from awesome_agent.context import (
    ContextBuilder,
    ContextOverflow,
    ContextRequest,
    ContextSkillIdentity,
    ContextSource,
    ContextSourceKind,
    calculate_context_budget,
    skill_identities_from_manifest,
)

_REVIEW_IDENTITY = ContextSkillIdentity(
    name="review",
    source="user",
    identity=f"skill-v1-sha256:{'a' * 64}",
    allowed_tools=("read_file",),
)


def _source(
    kind: ContextSourceKind,
    content: str,
    *,
    mandatory: bool = False,
    source_id: str | None = None,
    token_budget: int | None = None,
    skill_identities: tuple[ContextSkillIdentity, ...] = (),
) -> ContextSource:
    return ContextSource(
        kind=kind,
        source_id=source_id or kind.value,
        content=content,
        mandatory=mandatory,
        token_budget=token_budget,
        skill_identities=skill_identities,
    )


@pytest.mark.asyncio
async def test_builder_uses_stable_source_order_independent_of_input_order() -> None:
    sources = tuple(
        _source(
            kind,
            kind.value,
            mandatory=kind is ContextSourceKind.CURRENT_INPUT,
            source_id=(
                "review"
                if kind is ContextSourceKind.SKILL
                else "auto"
                if kind is ContextSourceKind.SKILL_CATALOG
                else None
            ),
            skill_identities=(
                (_REVIEW_IDENTITY,) if kind is ContextSourceKind.SKILL else ()
            ),
        )
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
async def test_conversation_sources_keep_sequence_roles_and_duplicate_text() -> None:
    prepared = await ContextBuilder().prepare(
        ContextRequest(
            sources=(
                ContextSource(
                    kind=ContextSourceKind.DIRECT_COMMAND,
                    source_id="direct-2",
                    content="command result",
                    role="assistant",
                    covered_sequence_start=2,
                    covered_sequence_end=2,
                ),
                ContextSource(
                    kind=ContextSourceKind.RECENT_TURNS,
                    source_id="assistant-3",
                    content="same transcript text",
                    role="assistant",
                    covered_sequence_start=3,
                    covered_sequence_end=3,
                ),
                ContextSource(
                    kind=ContextSourceKind.RECENT_TURNS,
                    source_id="user-1",
                    content="same transcript text",
                    role="user",
                    covered_sequence_start=1,
                    covered_sequence_end=1,
                ),
            ),
            configured_total_tokens=262_144,
            model_context_limit=262_144,
        )
    )

    assert [item.covered_sequence_start for item in prepared.manifest] == [1, 2, 3]
    assert [message.role for message in prepared.messages] == [
        "user",
        "assistant",
        "assistant",
    ]


@pytest.mark.asyncio
async def test_mandatory_instruction_sources_are_not_deduplicated() -> None:
    prepared = await ContextBuilder().prepare(
        ContextRequest(
            sources=(
                _source(
                    ContextSourceKind.PRODUCT_INSTRUCTIONS,
                    "same instructions",
                    mandatory=True,
                    source_id="product",
                ),
                _source(
                    ContextSourceKind.WORKSPACE_INSTRUCTIONS,
                    " same  instructions ",
                    mandatory=True,
                    source_id="AGENTS.md",
                ),
                _source(
                    ContextSourceKind.SKILL,
                    "same instructions",
                    mandatory=True,
                    source_id="review",
                    skill_identities=(_REVIEW_IDENTITY,),
                ),
            ),
            configured_total_tokens=262_144,
            model_context_limit=262_144,
        )
    )

    assert [(item.kind, item.source_id) for item in prepared.manifest] == [
        (ContextSourceKind.PRODUCT_INSTRUCTIONS, "product"),
        (ContextSourceKind.WORKSPACE_INSTRUCTIONS, "AGENTS.md"),
        (ContextSourceKind.SKILL, "review"),
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
async def test_reserved_tool_tail_reduces_base_context_capacity() -> None:
    budget = calculate_context_budget(40_000, 40_000)
    reserved = budget.effective_input_limit - 200

    prepared = await ContextBuilder().prepare(
        ContextRequest(
            sources=(
                _source(
                    ContextSourceKind.THREAD_SUMMARY,
                    "\n".join(f"history {index}" for index in range(1_000)),
                ),
                _source(
                    ContextSourceKind.CURRENT_INPUT,
                    "question",
                    mandatory=True,
                ),
            ),
            configured_total_tokens=40_000,
            model_context_limit=40_000,
            reserved_input_tokens=reserved,
        )
    )

    assert prepared.estimated_input_tokens + reserved <= budget.effective_input_limit
    assert prepared.manifest[0].truncated is True


@pytest.mark.asyncio
async def test_mandatory_base_plus_reserved_tool_tail_overflow_is_explicit() -> None:
    budget = calculate_context_budget(40_000, 40_000)

    with pytest.raises(ContextOverflow):
        await ContextBuilder().prepare(
            ContextRequest(
                sources=(
                    _source(
                        ContextSourceKind.CURRENT_INPUT,
                        "question",
                        mandatory=True,
                    ),
                ),
                configured_total_tokens=40_000,
                model_context_limit=40_000,
                reserved_input_tokens=budget.effective_input_limit,
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


@pytest.mark.asyncio
async def test_builder_freezes_structured_skill_identities_in_manifest() -> None:
    prepared = await ContextBuilder().prepare(
        ContextRequest(
            sources=(
                ContextSource(
                    kind=ContextSourceKind.SKILL_CATALOG,
                    source_id="auto",
                    content='{"skills":[{"name":"review"}]}',
                    role="system",
                    mandatory=True,
                    skill_identities=(_REVIEW_IDENTITY,),
                ),
            ),
            configured_total_tokens=262_144,
            model_context_limit=262_144,
        )
    )

    assert prepared.manifest[0].skill_identities == (_REVIEW_IDENTITY,)
    assert skill_identities_from_manifest(prepared.manifest) == (_REVIEW_IDENTITY,)


def test_skill_identities_are_scoped_and_malformed_manifests_fail_closed() -> None:
    with pytest.raises(ValueError, match="Only Skill context"):
        ContextSource(
            kind=ContextSourceKind.CURRENT_INPUT,
            source_id="input",
            content="question",
            skill_identities=(_REVIEW_IDENTITY,),
        )

    with pytest.raises(ValueError, match="must carry its own identity"):
        ContextSource(
            kind=ContextSourceKind.SKILL,
            source_id="review",
            content="instructions",
        )

    with pytest.raises(ValueError, match="source ID must be auto"):
        ContextSource(
            kind=ContextSourceKind.SKILL_CATALOG,
            source_id="other",
            content="catalog",
        )

    malformed = {
        "kind": "skill_catalog",
        "source_id": "auto",
        "order": 0,
        "estimated_tokens": 1,
        "truncated": False,
        "content_hash": "b" * 64,
        "skill_identities": [
            {
                **_REVIEW_IDENTITY.model_dump(mode="json"),
                "identity": "not-a-skill-identity",
            }
        ],
    }
    assert skill_identities_from_manifest((malformed,)) == ()
