from __future__ import annotations

import asyncio
import hashlib
import os
from uuid import uuid4

import pytest

from awesome_agent.config import KimiRegion
from awesome_agent.memory import (
    CloudDeleteStatus,
    Mem0CloudAdapter,
    Mem0Identity,
    MemoryCandidate,
    MemoryScope,
    create_mem0_client,
)
from awesome_agent.modeling import (
    ModelGateway,
    ModelRequest,
    RetryPolicy,
    SelectedModel,
    UserMessage,
)
from awesome_agent.providers import DeepSeekProvider, KimiProvider

pytestmark = [pytest.mark.external, pytest.mark.asyncio]


def _required(name: str) -> str:
    if os.environ.get("AWESOME_RUN_EXTERNAL") != "1":
        pytest.skip("external release checks are disabled")
    value = os.environ.get(name)
    if value is None or not value.strip():
        pytest.fail(f"required release credential is missing: {name}")
    return value


async def _no_sleep(delay: float) -> None:
    del delay


@pytest.mark.parametrize(
    ("provider", "model", "credential_name"),
    [
        ("deepseek", "deepseek/deepseek-v4-flash", "DEEPSEEK_API_KEY"),
        ("kimi", "kimi/kimi-k2.6", "MOONSHOT_API_KEY"),
    ],
)
async def test_live_provider_completes_one_small_turn(
    provider: str,
    model: str,
    credential_name: str,
) -> None:
    api_key = _required(credential_name)
    adapter = (
        DeepSeekProvider(api_key=api_key, model=model, timeout_seconds=60.0)
        if provider == "deepseek"
        else KimiProvider(
            api_key=api_key,
            model=model,
            region=KimiRegion.CN,
            timeout_seconds=60.0,
        )
    )
    gateway = ModelGateway(
        {adapter.provider_id: adapter},
        retry_policy=RetryPolicy(max_retries=0),
        sleeper=_no_sleep,
    )

    turn = await gateway.complete(
        SelectedModel(provider=adapter.provider_id, model=model),
        ModelRequest(
            messages=(UserMessage(content="Reply with exactly OK."),),
            max_output_tokens=32,
            thinking_enabled=False,
        ),
    )

    assert turn.assistant.content.strip()


async def test_live_mem0_add_recall_remove() -> None:
    api_key = _required("MEM0_API_KEY")
    token = uuid4().hex
    content = f"Awesome release validation {token}"
    identity = Mem0Identity(
        user_id=f"user_{uuid4().hex}",
        workspace_key=f"ws_{uuid4().hex}",
    )
    candidate = MemoryCandidate(
        scope=MemoryScope.WORKSPACE,
        content=content,
        fact_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
    )
    adapter = Mem0CloudAdapter(create_mem0_client(api_key), timeout_seconds=15.0)
    memory_id: str | None = None

    try:
        added = await adapter.add(candidate, identity)
        assert added.accepted is True
        memory_id = added.memory_id
        for _ in range(10):
            recalled = await adapter.search(
                token,
                user_id=identity.user_id,
                workspace_key=identity.workspace_key,
            )
            matched = next(
                (
                    memory
                    for memory in recalled
                    if memory.fact_hash == candidate.fact_hash
                ),
                None,
            )
            if matched is not None:
                memory_id = matched.id
                break
            await asyncio.sleep(2.0)
        assert memory_id is not None
    finally:
        if memory_id is not None:
            removed = await adapter.remove_scoped(memory_id, identity)
            assert removed.status is CloudDeleteStatus.REMOVED
