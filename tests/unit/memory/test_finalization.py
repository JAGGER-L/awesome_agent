from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest
from pydantic import ValidationError

from awesome_agent.agent import PostAnswerFinalizationRequest
from awesome_agent.memory import (
    CloudWriteOutcome,
    DistillationResult,
    DistillationStatus,
    Mem0Identity,
    Mem0PostAnswerFinalizer,
    MemoryCandidate,
    MemoryScope,
)
from awesome_agent.modeling import ModelUsage, SelectedModel


class Distiller:
    def __init__(self, result: DistillationResult) -> None:
        self.result = result
        self.calls: list[dict[str, object]] = []

    async def distill(self, **kwargs: object) -> DistillationResult:
        self.calls.append(dict(kwargs))
        return self.result


class CloudAdapter:
    def __init__(self) -> None:
        self.hashes: set[str] = set()
        self.add_calls = 0

    async def has_fact_hash(self, fact_hash: str, **kwargs: object) -> bool:
        del kwargs
        return fact_hash in self.hashes

    async def add(
        self,
        candidate: MemoryCandidate,
        identity: Mem0Identity,
    ) -> CloudWriteOutcome:
        del identity
        self.add_calls += 1
        self.hashes.add(candidate.fact_hash)
        return CloudWriteOutcome(accepted=True, memory_id="remote-1")


class StatusProjector:
    def __init__(self, *, cancel: bool = False, fail: bool = False) -> None:
        self.cancel = cancel
        self.fail = fail
        self.statuses: list[tuple[bool, str]] = []

    async def __call__(self, *, enabled: bool, status: str) -> None:
        self.statuses.append((enabled, status))
        if self.cancel:
            raise asyncio.CancelledError
        if self.fail:
            raise RuntimeError("private status failure")


@pytest.mark.asyncio
async def test_replay_after_remote_write_uses_fact_hash_deduplication() -> None:
    candidate = MemoryCandidate(
        scope=MemoryScope.USER,
        content="User prefers concise answers.",
        fact_hash="a" * 64,
    )
    distiller = Distiller(
        DistillationResult(
            status=DistillationStatus.COMPLETED,
            candidates=(candidate,),
            model_calls=1,
        )
    )
    adapter = CloudAdapter()
    statuses = StatusProjector()
    finalizer = Mem0PostAnswerFinalizer(
        distiller=cast(Any, distiller),
        adapter=cast(Any, adapter),
        identity=_identity(),
        project_status=statuses,
    )
    request = _request()

    first = await finalizer.finalize(request)
    second = await finalizer.finalize(request)

    assert first.final_answer == request.final_answer
    assert first.model_calls == second.model_calls == 1
    assert adapter.add_calls == 1
    assert statuses.statuses == [(True, "completed"), (True, "completed")]
    assert distiller.calls[0]["remaining_model_calls"] == 10
    assert distiller.calls[0]["remaining_provider_retries"] == 2


@pytest.mark.asyncio
async def test_scope_mismatch_is_a_generic_diagnostic_and_memory_status() -> None:
    distiller = Distiller(DistillationResult(status=DistillationStatus.COMPLETED))
    statuses = StatusProjector()
    finalizer = Mem0PostAnswerFinalizer(
        distiller=cast(Any, distiller),
        adapter=cast(Any, CloudAdapter()),
        identity=_identity(),
        project_status=statuses,
    )

    result = await finalizer.finalize(
        _request(workspace_key="ws_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")
    )

    assert distiller.calls == []
    assert statuses.statuses == [(True, "warning")]
    assert [item.code for item in result.diagnostics] == ["mem0_scope_mismatch"]
    assert all("Mem0" not in item.message for item in result.diagnostics)


@pytest.mark.asyncio
async def test_zero_budget_is_forwarded_and_reports_skipped_without_usage() -> None:
    distiller = Distiller(DistillationResult(status=DistillationStatus.SKIPPED))
    statuses = StatusProjector()
    finalizer = Mem0PostAnswerFinalizer(
        distiller=cast(Any, distiller),
        adapter=cast(Any, CloudAdapter()),
        identity=_identity(),
        project_status=statuses,
    )

    result = await finalizer.finalize(_request(remaining_model_calls=0))

    assert distiller.calls[0]["remaining_model_calls"] == 0
    assert result.model_calls == 0
    assert result.usage == ModelUsage()
    assert statuses.statuses == [(True, "skipped")]


@pytest.mark.asyncio
async def test_status_projection_failure_preserves_consumed_usage() -> None:
    distiller = Distiller(
        DistillationResult(
            status=DistillationStatus.COMPLETED,
            usage=ModelUsage(input_tokens=7, output_tokens=2),
            model_calls=1,
        )
    )
    statuses = StatusProjector(fail=True)
    finalizer = Mem0PostAnswerFinalizer(
        distiller=cast(Any, distiller),
        adapter=cast(Any, CloudAdapter()),
        identity=_identity(),
        project_status=statuses,
    )

    result = await finalizer.finalize(_request())

    assert result.model_calls == 1
    assert result.usage.input_tokens == 7
    assert [item.code for item in result.diagnostics] == [
        "memory_status_projection_failed"
    ]
    assert statuses.statuses == [(True, "completed")]


@pytest.mark.asyncio
async def test_status_projection_cancellation_is_propagated() -> None:
    distiller = Distiller(DistillationResult(status=DistillationStatus.COMPLETED))
    statuses = StatusProjector(cancel=True)
    finalizer = Mem0PostAnswerFinalizer(
        distiller=cast(Any, distiller),
        adapter=cast(Any, CloudAdapter()),
        identity=_identity(),
        project_status=statuses,
    )

    with pytest.raises(asyncio.CancelledError):
        await finalizer.finalize(_request())

    assert statuses.statuses == [(True, "completed")]


@pytest.mark.asyncio
async def test_invalid_memory_budget_result_is_rejected_before_status() -> None:
    distiller = Distiller(
        DistillationResult(
            status=DistillationStatus.COMPLETED,
            usage=ModelUsage(provider_retries=1),
            model_calls=1,
        )
    )
    statuses = StatusProjector()
    finalizer = Mem0PostAnswerFinalizer(
        distiller=cast(Any, distiller),
        adapter=cast(Any, CloudAdapter()),
        identity=_identity(),
        project_status=statuses,
    )

    with pytest.raises(ValueError, match="model call budget"):
        await finalizer.finalize(_request(remaining_model_calls=1))

    assert statuses.statuses == []


@pytest.mark.asyncio
async def test_constructed_invalid_distillation_usage_is_revalidated() -> None:
    distiller = Distiller(
        DistillationResult.model_construct(
            status=DistillationStatus.COMPLETED,
            candidates=(),
            usage=ModelUsage.model_construct(input_tokens=-1),
            model_calls=1,
            diagnostic=None,
        )
    )
    statuses = StatusProjector()
    finalizer = Mem0PostAnswerFinalizer(
        distiller=cast(Any, distiller),
        adapter=cast(Any, CloudAdapter()),
        identity=_identity(),
        project_status=statuses,
    )

    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        await finalizer.finalize(_request())

    assert statuses.statuses == []


@pytest.mark.asyncio
async def test_duck_typed_distillation_usage_is_rejected_before_status() -> None:
    class FakeUsage:
        input_tokens = 1
        output_tokens = 0
        reasoning_tokens = 0
        cache_read_tokens = 0
        cache_write_tokens = 0
        provider_retries = 0

    distiller = Distiller(
        DistillationResult.model_construct(
            status=DistillationStatus.COMPLETED,
            candidates=(),
            usage=FakeUsage(),
            model_calls=1,
            diagnostic=None,
        )
    )
    statuses = StatusProjector()
    finalizer = Mem0PostAnswerFinalizer(
        distiller=cast(Any, distiller),
        adapter=cast(Any, CloudAdapter()),
        identity=_identity(),
        project_status=statuses,
    )

    with pytest.raises(TypeError, match="invalid usage contract"):
        await finalizer.finalize(_request())

    assert statuses.statuses == []


def _identity() -> Mem0Identity:
    return Mem0Identity(
        user_id="user_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        workspace_key="ws_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    )


def _request(
    *,
    workspace_key: str = "ws_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    remaining_model_calls: int = 10,
) -> PostAnswerFinalizationRequest:
    return PostAnswerFinalizationRequest(
        user_text="remember concise answers",
        final_answer="done",
        selected_model=SelectedModel(
            provider="deepseek",
            model="deepseek/deepseek-v4-flash",
        ),
        remaining_model_calls=remaining_model_calls,
        remaining_provider_retries=2,
        workspace_key=workspace_key,
    )
