from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import empty_checkpoint

from awesome_agent.agent import new_agent_state
from awesome_agent.storage.checkpoints import (
    CheckpointCorrupt,
    LangGraphCheckpointStore,
    sqlite_checkpoint_saver,
)


async def test_sqlite_checkpoint_survives_saver_reopen(tmp_path: Path) -> None:
    database_path = tmp_path / "state" / "checkpoints.db"
    config: RunnableConfig = {
        "configurable": {
            "thread_id": str(uuid4()),
            "checkpoint_ns": "",
        }
    }
    checkpoint = empty_checkpoint()

    async with sqlite_checkpoint_saver(database_path) as saver:
        stored_config = await saver.aput(config, checkpoint, {}, {})

    assert database_path.is_file()

    async with sqlite_checkpoint_saver(database_path) as saver:
        stored = await saver.aget_tuple(stored_config)

    assert stored is not None
    assert stored.checkpoint["id"] == checkpoint["id"]


async def test_checkpoint_store_reads_valid_state_rejects_corrupt_and_deletes(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "checkpoints.db"
    valid_id = "turn_valid"
    corrupt_id = "turn_corrupt"
    valid = empty_checkpoint()
    state = new_agent_state(
        thread_id="thread_1",
        turn_id=valid_id,
        workspace_key="workspace_1",
        provider="deepseek",
        model="deepseek/deepseek-v4-flash",
        thinking_enabled=False,
    )
    channel_values: dict[str, Any] = dict(state)
    valid["channel_values"] = channel_values
    corrupt = empty_checkpoint()
    corrupt["channel_values"] = {"unexpected": True}

    async with sqlite_checkpoint_saver(database_path) as saver:
        await saver.aput(
            {"configurable": {"thread_id": valid_id, "checkpoint_ns": ""}},
            valid,
            {},
            {},
        )
        await saver.aput(
            {"configurable": {"thread_id": corrupt_id, "checkpoint_ns": ""}},
            corrupt,
            {},
            {},
        )
        store = LangGraphCheckpointStore(saver)

        assert await store.latest_state(valid_id) == state
        with pytest.raises(CheckpointCorrupt):
            await store.latest_state(corrupt_id)
        await store.delete(valid_id)
        assert await store.exists(valid_id) is False
