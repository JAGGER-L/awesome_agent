from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Protocol

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from awesome_agent.agent import AgentState, validate_agent_state


class CheckpointCorrupt(RuntimeError):
    pass


class TurnCheckpointStore(Protocol):
    async def exists(self, turn_id: str) -> bool: ...
    async def latest_state(self, turn_id: str) -> AgentState | None: ...
    async def delete(self, turn_id: str) -> None: ...


class LangGraphCheckpointStore:
    def __init__(self, saver: BaseCheckpointSaver[str]) -> None:
        self._saver = saver

    async def exists(self, turn_id: str) -> bool:
        return await self._saver.aget_tuple(_config(turn_id)) is not None

    async def latest_state(self, turn_id: str) -> AgentState | None:
        checkpoint = await self._saver.aget_tuple(_config(turn_id))
        if checkpoint is None:
            return None
        try:
            values = checkpoint.checkpoint["channel_values"]
            return validate_agent_state(values)
        except (KeyError, TypeError, ValueError) as error:
            raise CheckpointCorrupt(turn_id) from error

    async def delete(self, turn_id: str) -> None:
        await self._saver.adelete_thread(turn_id)


def _config(turn_id: str) -> RunnableConfig:
    return {"configurable": {"thread_id": turn_id, "checkpoint_ns": ""}}


@asynccontextmanager
async def sqlite_checkpoint_saver(
    path: Path,
) -> AsyncIterator[AsyncSqliteSaver]:
    database_path = path.expanduser().resolve()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(str(database_path)) as saver:
        yield saver
