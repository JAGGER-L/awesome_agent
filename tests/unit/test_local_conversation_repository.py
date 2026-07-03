from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from awesome_agent.conversation.models import (
    ThreadMessageKind,
    ThreadMessageRole,
)
from awesome_agent.persistence.local_conversations import LocalConversationRepository


@pytest.mark.asyncio
async def test_local_repository_persists_threads_across_instances(
    tmp_path: Path,
) -> None:
    db = tmp_path / "awesome-agent.db"
    first = LocalConversationRepository(db)
    thread = await first.create_thread(
        title="Snake game",
        context_kind="repo",
        context_path=str(tmp_path),
    )
    await first.append_message(
        thread_id=thread.id,
        role=ThreadMessageRole.USER,
        content="hi",
    )
    first.close()

    second = LocalConversationRepository(db)
    threads = await second.list_threads()
    messages = await second.list_messages(thread.id)
    second.close()

    assert threads[0].id == thread.id
    assert threads[0].context_kind == "repo"
    assert messages[0].content == "hi"


@pytest.mark.asyncio
async def test_local_repository_resolves_by_id_and_title(tmp_path: Path) -> None:
    repository = LocalConversationRepository(tmp_path / "awesome-agent.db")
    thread = await repository.create_thread(title="Snake game")

    by_id = await repository.resolve_thread(str(thread.id))
    by_title = await repository.resolve_thread("Snake")
    repository.close()

    assert by_id.id == thread.id
    assert by_title.id == thread.id


@pytest.mark.asyncio
async def test_local_repository_updates_thread_on_message_append(
    tmp_path: Path,
) -> None:
    repository = LocalConversationRepository(tmp_path / "awesome-agent.db")
    thread = await repository.create_thread(title="Thread")

    await repository.append_message(
        thread_id=thread.id,
        role=ThreadMessageRole.ASSISTANT,
        content="Done.",
        kind=ThreadMessageKind.MESSAGE,
        metadata={
            "changed_files": [
                {
                    "path": "/mnt/user-data/workspace/snake.html",
                    "status": "created",
                }
            ]
        },
    )
    [updated] = await repository.list_threads()
    repository.close()

    assert updated.updated_at >= thread.updated_at


@pytest.mark.asyncio
async def test_local_repository_persists_settings_and_changed_files(
    tmp_path: Path,
) -> None:
    db = tmp_path / "awesome-agent.db"
    repository = LocalConversationRepository(db)
    thread = await repository.create_thread(
        title="Settings",
        default_model="deepseek-v4-pro",
        thinking_mode="on_high",
        local_memory_enabled=True,
        provider_memory="mem0",
    )
    await repository.update_thread_settings(
        thread.id,
        default_model="deepseek-v4-flash",
        thinking_mode="off",
        local_memory_enabled=False,
        provider_memory=None,
    )
    await repository.append_message(
        thread_id=thread.id,
        role=ThreadMessageRole.ASSISTANT,
        content="Done.",
        metadata={
            "changed_files": [{"path": "README.md", "status": "updated"}],
        },
    )
    repository.close()

    reopened = LocalConversationRepository(db)
    [stored] = await reopened.list_threads()
    messages = await reopened.list_messages(thread.id)
    reopened.close()

    assert stored.default_model == "deepseek-v4-flash"
    assert stored.thinking_mode == "off"
    assert stored.local_memory_enabled is False
    assert stored.provider_memory is None
    assert messages[0].metadata["changed_files"] == [
        {"path": "README.md", "status": "updated"}
    ]


@pytest.mark.asyncio
async def test_local_repository_binds_repository_id(tmp_path: Path) -> None:
    repository = LocalConversationRepository(tmp_path / "awesome-agent.db")
    thread = await repository.create_thread(title="Repo")
    repository_id = uuid4()

    updated = await repository.bind_repository(thread.id, repository_id)
    repository.close()

    assert updated.repository_id == repository_id
