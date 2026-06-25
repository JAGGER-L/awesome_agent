from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class RepositoryRecord(Base):
    __tablename__ = "repositories"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    root: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str] = mapped_column(String(255))
    git_common_dir: Mapped[str] = mapped_column(Text, unique=True)
    default_branch: Mapped[str | None] = mapped_column(String(255))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RunRecord(Base):
    __tablename__ = "runs"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    goal: Mapped[str] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), index=True)
    repository_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("repositories.id", ondelete="RESTRICT"),
        index=True,
    )
    base_commit: Mapped[str | None] = mapped_column(String(64))
    intent: Mapped[str] = mapped_column(String(32))
    dispatch_status: Mapped[str] = mapped_column(String(32), index=True)
    workspace_path: Mapped[str | None] = mapped_column(Text)
    integration_branch: Mapped[str | None] = mapped_column(String(255))
    workspace_state: Mapped[str | None] = mapped_column(String(32))
    graph_thread_id: Mapped[str | None] = mapped_column(String(128), unique=True)
    legacy: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class IntakeReservationRecord(Base):
    __tablename__ = "intake_reservations"
    __table_args__ = (
        Index(
            "ix_intake_reservations_repository_branch",
            "repository_id",
            "integration_branch",
            unique=True,
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), unique=True)
    repository_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("repositories.id", ondelete="RESTRICT"),
        index=True,
    )
    base_commit: Mapped[str] = mapped_column(String(64))
    intent: Mapped[str] = mapped_column(String(32))
    workspace_path: Mapped[str] = mapped_column(Text, unique=True)
    integration_branch: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), index=True)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AgentRecord(Base):
    __tablename__ = "agents"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    run_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), index=True
    )
    parent_agent_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL")
    )
    kind: Mapped[str] = mapped_column(String(32))
    profile: Mapped[str] = mapped_column(String(128))
    model: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TodoRecord(Base):
    __tablename__ = "todos"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    run_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), index=True
    )
    parent_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("todos.id", ondelete="CASCADE")
    )
    title: Mapped[str] = mapped_column(String(512))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), index=True)
    primary_owner_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL")
    )
    collaborator_ids: Mapped[list[str]] = mapped_column(JSONB, default=list)
    acceptance_criteria: Mapped[list[str]] = mapped_column(JSONB, default=list)
    blocker: Mapped[str | None] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RuntimeEventRecord(Base):
    __tablename__ = "runtime_events"
    __table_args__ = (
        Index("ix_runtime_events_run_sequence", "run_id", "sequence", unique=True),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    run_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), index=True
    )
    sequence: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(128), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    team_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    agent_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), index=True)
    parent_agent_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    task_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), index=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), index=True)
    span_id: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
