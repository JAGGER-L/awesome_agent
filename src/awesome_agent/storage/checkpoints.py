from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver


@asynccontextmanager
async def sqlite_checkpoint_saver(
    path: Path,
) -> AsyncIterator[AsyncSqliteSaver]:
    database_path = path.expanduser().resolve()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(str(database_path)) as saver:
        yield saver
