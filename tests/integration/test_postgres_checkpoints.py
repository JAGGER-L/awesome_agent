from __future__ import annotations

import os
from uuid import uuid4

import pytest
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import empty_checkpoint

from awesome_agent.persistence.checkpoints import checkpoint_saver

pytestmark = pytest.mark.integration


@pytest.mark.skipif(
    "AWESOME_AGENT_TEST_CHECKPOINT_DATABASE_URL" not in os.environ,
    reason="Checkpoint database is not configured.",
)
async def test_checkpoint_round_trip() -> None:
    connection_url = os.environ["AWESOME_AGENT_TEST_CHECKPOINT_DATABASE_URL"]
    config: RunnableConfig = {
        "configurable": {
            "thread_id": str(uuid4()),
            "checkpoint_ns": "",
        }
    }
    checkpoint = empty_checkpoint()

    async with checkpoint_saver(connection_url) as saver:
        await saver.setup()
        stored_config = await saver.aput(config, checkpoint, {}, {})
        stored = await saver.aget_tuple(stored_config)

    assert stored is not None
    assert stored.checkpoint["id"] == checkpoint["id"]
