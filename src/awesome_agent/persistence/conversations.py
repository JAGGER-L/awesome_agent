from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from awesome_agent.conversation.models import (
    ThreadMessage,
    ThreadMessageKind,
    ThreadMessageRole,
)
from awesome_agent.domain.threads import Thread
from awesome_agent.persistence.models import ThreadMessageRecord, ThreadRecord


class InMemoryConversationRepository:
    def __init__(self) -> None:
        self._threads: dict[UUID, Thread] = {}
        self._messages: dict[UUID, list[ThreadMessage]] = {}

    async def create_thread(
        self,
        *,
        title: str,
        context_kind: str = "workspace",
        context_path: str | None = None,
        default_model: str | None = None,
        sandbox_profile: str | None = None,
    ) -> Thread:
        thread = Thread(
            title=title,
            context_kind=context_kind,
            context_path=context_path,
            default_model=default_model,
            sandbox_profile=sandbox_profile,
        )
        self._threads[thread.id] = thread
        self._messages[thread.id] = []
        return thread

    async def list_threads(self) -> list[Thread]:
        return sorted(
            self._threads.values(),
            key=lambda thread: (thread.updated_at, thread.created_at),
            reverse=True,
        )

    async def get_thread(self, thread_id: UUID) -> Thread:
        try:
            return self._threads[thread_id]
        except KeyError as error:
            raise KeyError(f"Thread not found: {thread_id}") from error

    async def resolve_thread(self, query: str) -> Thread:
        try:
            return await self.get_thread(UUID(query))
        except (ValueError, KeyError):
            pass
        normalized = query.casefold()
        for thread in await self.list_threads():
            if normalized in thread.title.casefold():
                return thread
        raise KeyError(f"Thread not found: {query}")

    async def append_message(
        self,
        *,
        thread_id: UUID,
        role: ThreadMessageRole,
        content: str,
        kind: ThreadMessageKind = ThreadMessageKind.MESSAGE,
        run_id: UUID | None = None,
        metadata: dict[str, object] | None = None,
    ) -> ThreadMessage:
        thread = await self.get_thread(thread_id)
        messages = self._messages.setdefault(thread_id, [])
        message = ThreadMessage(
            thread_id=thread_id,
            role=role,
            content=content,
            kind=kind,
            run_id=run_id,
            metadata=metadata or {},
            sequence=len(messages) + 1,
        )
        messages.append(message)
        self._threads[thread_id] = thread.model_copy(
            update={"updated_at": message.created_at}
        )
        return message

    async def list_messages(self, thread_id: UUID) -> list[ThreadMessage]:
        await self.get_thread(thread_id)
        return list(self._messages.get(thread_id, []))


class PostgresConversationRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def create_thread(
        self,
        *,
        title: str,
        context_kind: str = "workspace",
        context_path: str | None = None,
        default_model: str | None = None,
        sandbox_profile: str | None = None,
    ) -> Thread:
        thread = Thread(
            title=title,
            context_kind=context_kind,
            context_path=context_path,
            default_model=default_model,
            sandbox_profile=sandbox_profile,
        )
        async with self._sessions.begin() as session:
            session.add(_thread_to_record(thread))
        return thread

    async def list_threads(self) -> list[Thread]:
        async with self._sessions() as session:
            records = list(
                await session.scalars(
                    select(ThreadRecord).order_by(
                        ThreadRecord.updated_at.desc(),
                        ThreadRecord.created_at.desc(),
                        ThreadRecord.id.desc(),
                    )
                )
            )
        return [_thread_from_record(record) for record in records]

    async def get_thread(self, thread_id: UUID) -> Thread:
        async with self._sessions() as session:
            record = await session.get(ThreadRecord, thread_id)
        if record is None:
            raise KeyError(f"Thread not found: {thread_id}")
        return _thread_from_record(record)

    async def resolve_thread(self, query: str) -> Thread:
        try:
            return await self.get_thread(UUID(query))
        except (ValueError, KeyError):
            pass
        async with self._sessions() as session:
            record = await session.scalar(
                select(ThreadRecord)
                .where(ThreadRecord.title.ilike(f"%{query}%"))
                .order_by(
                    ThreadRecord.updated_at.desc(),
                    ThreadRecord.created_at.desc(),
                    ThreadRecord.id.desc(),
                )
            )
        if record is None:
            raise KeyError(f"Thread not found: {query}")
        return _thread_from_record(record)

    async def append_message(
        self,
        *,
        thread_id: UUID,
        role: ThreadMessageRole,
        content: str,
        kind: ThreadMessageKind = ThreadMessageKind.MESSAGE,
        run_id: UUID | None = None,
        metadata: dict[str, object] | None = None,
    ) -> ThreadMessage:
        async with self._sessions.begin() as session:
            thread_record = await session.get(ThreadRecord, thread_id)
            if thread_record is None:
                raise KeyError(f"Thread not found: {thread_id}")
            next_sequence = (
                await session.scalar(
                    select(
                        func.coalesce(func.max(ThreadMessageRecord.sequence), 0)
                    ).where(ThreadMessageRecord.thread_id == thread_id)
                )
            ) + 1
            message = ThreadMessage(
                thread_id=thread_id,
                role=role,
                content=content,
                kind=kind,
                run_id=run_id,
                metadata=metadata or {},
                sequence=next_sequence,
            )
            session.add(_message_to_record(message))
            thread_record.updated_at = message.created_at
        return message

    async def list_messages(self, thread_id: UUID) -> list[ThreadMessage]:
        await self.get_thread(thread_id)
        async with self._sessions() as session:
            records = list(
                await session.scalars(
                    select(ThreadMessageRecord)
                    .where(ThreadMessageRecord.thread_id == thread_id)
                    .order_by(ThreadMessageRecord.sequence)
                )
            )
        return [_message_from_record(record) for record in records]


def _thread_to_record(thread: Thread) -> ThreadRecord:
    return ThreadRecord(
        id=thread.id,
        title=thread.title,
        context_kind=thread.context_kind,
        context_path=thread.context_path,
        default_model=thread.default_model,
        sandbox_profile=thread.sandbox_profile,
        created_at=thread.created_at,
        updated_at=thread.updated_at,
    )


def _thread_from_record(record: ThreadRecord) -> Thread:
    return Thread(
        id=record.id,
        title=record.title,
        context_kind=record.context_kind,
        context_path=record.context_path,
        default_model=record.default_model,
        sandbox_profile=record.sandbox_profile,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _message_to_record(message: ThreadMessage) -> ThreadMessageRecord:
    return ThreadMessageRecord(
        id=message.id,
        thread_id=message.thread_id,
        role=message.role.value,
        content=message.content,
        kind=message.kind.value,
        run_id=message.run_id,
        message_metadata=message.metadata,
        sequence=message.sequence,
        created_at=message.created_at,
    )


def _message_from_record(record: ThreadMessageRecord) -> ThreadMessage:
    return ThreadMessage(
        id=record.id,
        thread_id=record.thread_id,
        role=ThreadMessageRole(record.role),
        content=record.content,
        kind=ThreadMessageKind(record.kind),
        run_id=record.run_id,
        metadata=record.message_metadata or {},
        sequence=record.sequence,
        created_at=record.created_at,
    )
