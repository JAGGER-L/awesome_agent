from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Protocol

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from awesome_agent.agent import AgentState, validate_agent_state

_AGENT_STATE_KEYS = (
    "thread_id",
    "turn_id",
    "workspace_key",
    "provider",
    "model",
    "thinking_enabled",
    "context_manifest",
    "context_estimated_tokens",
    "context_effective_limit",
    "compression_requested",
    "compression_reason",
    "messages",
    "continuation",
    "pending_tool_calls",
    "next_tool_index",
    "tool_results",
    "model_calls",
    "tool_calls",
    "provider_retries",
    "compressions",
    "active_execution_seconds",
    "usage",
    "recovery_issue",
    "final_answer",
    "termination_reason",
)
_AGENT_STATE_KEY_SET = frozenset(_AGENT_STATE_KEYS)


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
            if not isinstance(values, dict):
                raise TypeError("checkpoint channel values must be a mapping")
            keys = frozenset(values)
            if not _AGENT_STATE_KEY_SET.issubset(keys):
                raise ValueError("checkpoint is missing AgentState channels")
            unexpected = keys - _AGENT_STATE_KEY_SET
            if any(
                not isinstance(key, str) or not key.startswith("branch:to:")
                for key in unexpected
            ):
                raise ValueError("checkpoint contains an unknown public channel")
            projected = {key: values[key] for key in _AGENT_STATE_KEYS}
            return validate_agent_state(projected)
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
