from __future__ import annotations

import os

import pytest

from awesome_agent.conversation.models import ThreadMessageRole
from awesome_agent.persistence.conversations import PostgresConversationRepository
from awesome_agent.persistence.database import create_engine, create_session_factory
from awesome_agent.persistence.models import Base

pytestmark = pytest.mark.integration


@pytest.mark.skipif(
    "AWESOME_AGENT_TEST_DATABASE_URL" not in os.environ,
    reason="Integration database is not configured.",
)
async def test_conversation_messages_survive_repository_restart() -> None:
    engine = create_engine(os.environ["AWESOME_AGENT_TEST_DATABASE_URL"])
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = create_session_factory(engine)
    first_repository = PostgresConversationRepository(sessions)
    thread = await first_repository.create_thread(
        title="Snake game",
        context_kind="repo",
        context_path="E:/games/snake",
        default_model="deepseek-v4-pro",
        sandbox_profile="aio-docker",
    )
    await first_repository.append_message(
        thread_id=thread.id,
        role=ThreadMessageRole.USER,
        content="Build a snake game.",
    )

    second_repository = PostgresConversationRepository(sessions)
    loaded = await second_repository.get_thread(thread.id)
    messages = await second_repository.list_messages(thread.id)

    assert loaded.title == "Snake game"
    assert loaded.context_kind == "repo"
    assert loaded.context_path == "E:/games/snake"
    assert loaded.default_model == "deepseek-v4-pro"
    assert loaded.sandbox_profile == "aio-docker"
    assert len(messages) == 1
    assert messages[0].sequence == 1
    assert messages[0].content == "Build a snake game."
    await engine.dispose()
