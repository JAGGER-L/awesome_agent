from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import empty_checkpoint

from awesome_agent.storage.checkpoints import sqlite_checkpoint_saver


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
