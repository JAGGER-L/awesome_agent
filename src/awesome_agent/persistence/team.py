from __future__ import annotations

from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from awesome_agent.persistence.models import (
    TeamAssignmentRecord,
    TeamChildResultRecord,
    TeamMailboxMessageRecord,
)
from awesome_agent.runtime.team_assignments import (
    TeamAssignment,
    TeamAssignmentKind,
    TeamAssignmentStatus,
    TeamChildResult,
)
from awesome_agent.runtime.team_mailbox import (
    MailboxMessage,
    MailboxMessageStatus,
    MailboxMessageType,
    MailboxRoute,
)


class TeamRepository(Protocol):
    async def create_assignment(self, assignment: TeamAssignment) -> TeamAssignment: ...

    async def get_assignment(self, assignment_id: UUID) -> TeamAssignment: ...

    async def get_assignment_for_child_run(
        self, child_run_id: UUID
    ) -> TeamAssignment: ...

    async def list_assignments(
        self,
        root_run_id: UUID,
        *,
        include_inactive: bool = False,
    ) -> list[TeamAssignment]: ...

    async def retire_assignment(
        self,
        assignment_id: UUID,
        *,
        reason: str,
    ) -> TeamAssignment: ...

    async def record_child_terminal(
        self,
        child_run_id: UUID,
        *,
        status: TeamAssignmentStatus,
    ) -> UUID | None: ...

    async def record_child_result(self, result: TeamChildResult) -> TeamChildResult: ...

    async def list_child_results(
        self,
        parent_run_id: UUID,
    ) -> list[TeamChildResult]: ...

    async def mark_child_result_patch_aggregated(
        self,
        child_run_id: UUID,
    ) -> TeamChildResult: ...

    async def create_mailbox_message(
        self, message: MailboxMessage
    ) -> MailboxMessage: ...

    async def get_mailbox_message(self, message_id: UUID) -> MailboxMessage: ...

    async def mark_mailbox_read(self, message_id: UUID) -> MailboxMessage: ...

    async def respond_to_mailbox_message(
        self,
        message_id: UUID,
        response: MailboxMessage,
    ) -> tuple[MailboxMessage, MailboxMessage]: ...

    async def list_mailbox_messages(
        self,
        run_id: UUID,
        *,
        include_archived: bool = False,
    ) -> list[MailboxMessage]: ...


class InMemoryTeamRepository(TeamRepository):
    def __init__(self) -> None:
        self._assignments: dict[UUID, TeamAssignment] = {}
        self._mailbox: dict[UUID, MailboxMessage] = {}
        self._child_results: dict[UUID, TeamChildResult] = {}

    async def create_assignment(self, assignment: TeamAssignment) -> TeamAssignment:
        self._assignments[assignment.id] = assignment
        return assignment

    async def get_assignment(self, assignment_id: UUID) -> TeamAssignment:
        return self._assignments[assignment_id]

    async def get_assignment_for_child_run(self, child_run_id: UUID) -> TeamAssignment:
        for assignment in self._assignments.values():
            if assignment.child_run_id == child_run_id:
                return assignment
        raise KeyError(child_run_id)

    async def list_assignments(
        self,
        root_run_id: UUID,
        *,
        include_inactive: bool = False,
    ) -> list[TeamAssignment]:
        assignments = [
            assignment
            for assignment in self._assignments.values()
            if assignment.root_run_id == root_run_id
        ]
        if not include_inactive:
            assignments = [
                assignment
                for assignment in assignments
                if assignment.status is TeamAssignmentStatus.ACTIVE
            ]
        return sorted(assignments, key=lambda item: (item.created_at, item.id.hex))

    async def retire_assignment(
        self,
        assignment_id: UUID,
        *,
        reason: str,
    ) -> TeamAssignment:
        assignment = self._assignments[assignment_id].model_copy(
            update={
                "status": TeamAssignmentStatus.RETIRED,
                "retire_reason": reason,
            }
        )
        self._assignments[assignment_id] = assignment
        return assignment

    async def record_child_terminal(
        self,
        child_run_id: UUID,
        *,
        status: TeamAssignmentStatus,
    ) -> UUID | None:
        assignment = await self.get_assignment_for_child_run(child_run_id)
        updated = assignment.model_copy(update={"status": status})
        self._assignments[assignment.id] = updated
        siblings = [
            item
            for item in self._assignments.values()
            if item.parent_run_id == assignment.parent_run_id
        ]
        if all(item.status is not TeamAssignmentStatus.ACTIVE for item in siblings):
            return assignment.parent_run_id
        return None

    async def create_mailbox_message(self, message: MailboxMessage) -> MailboxMessage:
        self._mailbox[message.id] = message
        return message

    async def record_child_result(self, result: TeamChildResult) -> TeamChildResult:
        self._child_results[result.child_run_id] = result
        return result

    async def list_child_results(self, parent_run_id: UUID) -> list[TeamChildResult]:
        return sorted(
            [
                result
                for result in self._child_results.values()
                if result.parent_run_id == parent_run_id
            ],
            key=lambda item: (item.created_at, item.child_run_id.hex),
        )

    async def mark_child_result_patch_aggregated(
        self,
        child_run_id: UUID,
    ) -> TeamChildResult:
        result = self._child_results[child_run_id].model_copy(
            update={"patch_aggregated": True}
        )
        self._child_results[child_run_id] = result
        return result

    async def get_mailbox_message(self, message_id: UUID) -> MailboxMessage:
        return self._mailbox[message_id]

    async def mark_mailbox_read(self, message_id: UUID) -> MailboxMessage:
        from awesome_agent.domain.models import utc_now

        message = self._mailbox[message_id].model_copy(
            update={
                "status": MailboxMessageStatus.READ,
                "read_at": utc_now(),
            }
        )
        self._mailbox[message_id] = message
        return message

    async def respond_to_mailbox_message(
        self,
        message_id: UUID,
        response: MailboxMessage,
    ) -> tuple[MailboxMessage, MailboxMessage]:
        from awesome_agent.domain.models import utc_now

        original = self._mailbox[message_id].model_copy(
            update={
                "status": MailboxMessageStatus.RESPONDED,
                "responded_at": utc_now(),
            }
        )
        response = response.model_copy(update={"response_to_message_id": message_id})
        self._mailbox[message_id] = original
        self._mailbox[response.id] = response
        return original, response

    async def list_mailbox_messages(
        self,
        run_id: UUID,
        *,
        include_archived: bool = False,
    ) -> list[MailboxMessage]:
        messages = [
            message
            for message in self._mailbox.values()
            if message.sender_run_id == run_id or message.recipient_run_id == run_id
        ]
        if not include_archived:
            messages = [
                message
                for message in messages
                if message.status is not MailboxMessageStatus.ARCHIVED
            ]
        return sorted(messages, key=lambda item: (item.created_at, item.id.hex))


class PostgresTeamRepository(TeamRepository):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def create_assignment(self, assignment: TeamAssignment) -> TeamAssignment:
        async with self._sessions.begin() as session:
            session.add(_assignment_to_record(assignment))
        return assignment

    async def get_assignment(self, assignment_id: UUID) -> TeamAssignment:
        async with self._sessions() as session:
            record = await session.get(TeamAssignmentRecord, assignment_id)
        if record is None:
            raise KeyError(assignment_id)
        return _assignment_from_record(record)

    async def get_assignment_for_child_run(self, child_run_id: UUID) -> TeamAssignment:
        async with self._sessions() as session:
            record = await session.scalar(
                select(TeamAssignmentRecord).where(
                    TeamAssignmentRecord.child_run_id == child_run_id
                )
            )
        if record is None:
            raise KeyError(child_run_id)
        return _assignment_from_record(record)

    async def list_assignments(
        self,
        root_run_id: UUID,
        *,
        include_inactive: bool = False,
    ) -> list[TeamAssignment]:
        statement = select(TeamAssignmentRecord).where(
            TeamAssignmentRecord.root_run_id == root_run_id
        )
        if not include_inactive:
            statement = statement.where(
                TeamAssignmentRecord.status == TeamAssignmentStatus.ACTIVE.value
            )
        statement = statement.order_by(
            TeamAssignmentRecord.created_at,
            TeamAssignmentRecord.id,
        )
        async with self._sessions() as session:
            records = list(await session.scalars(statement))
        return [_assignment_from_record(record) for record in records]

    async def retire_assignment(
        self,
        assignment_id: UUID,
        *,
        reason: str,
    ) -> TeamAssignment:
        async with self._sessions.begin() as session:
            record = await session.get(TeamAssignmentRecord, assignment_id)
            if record is None:
                raise KeyError(assignment_id)
            record.status = TeamAssignmentStatus.RETIRED.value
            record.retire_reason = reason
            return _assignment_from_record(record)

    async def record_child_terminal(
        self,
        child_run_id: UUID,
        *,
        status: TeamAssignmentStatus,
    ) -> UUID | None:
        async with self._sessions.begin() as session:
            record = await session.scalar(
                select(TeamAssignmentRecord)
                .where(TeamAssignmentRecord.child_run_id == child_run_id)
                .with_for_update()
            )
            if record is None:
                raise KeyError(child_run_id)
            record.status = status.value
            siblings = list(
                await session.scalars(
                    select(TeamAssignmentRecord)
                    .where(TeamAssignmentRecord.parent_run_id == record.parent_run_id)
                    .with_for_update()
                )
            )
            if all(
                sibling.status != TeamAssignmentStatus.ACTIVE.value
                for sibling in siblings
            ):
                return record.parent_run_id
            return None

    async def create_mailbox_message(self, message: MailboxMessage) -> MailboxMessage:
        async with self._sessions.begin() as session:
            session.add(_mailbox_to_record(message))
        return message

    async def record_child_result(self, result: TeamChildResult) -> TeamChildResult:
        async with self._sessions.begin() as session:
            existing = await session.get(TeamChildResultRecord, result.child_run_id)
            if existing is None:
                session.add(_child_result_to_record(result))
            else:
                existing.status = result.status
                existing.summary = result.summary
                existing.patch_artifact_id = result.patch_artifact_id
                existing.changed_files = result.changed_files
                existing.evidence_artifact_refs = [
                    str(value) for value in result.evidence_artifact_refs
                ]
                existing.failure_kind = result.failure_kind
                existing.patch_aggregated = result.patch_aggregated
                existing.updated_at = result.updated_at
        return result

    async def list_child_results(self, parent_run_id: UUID) -> list[TeamChildResult]:
        async with self._sessions() as session:
            records = list(
                await session.scalars(
                    select(TeamChildResultRecord)
                    .where(TeamChildResultRecord.parent_run_id == parent_run_id)
                    .order_by(
                        TeamChildResultRecord.created_at,
                        TeamChildResultRecord.child_run_id,
                    )
                )
            )
        return [_child_result_from_record(record) for record in records]

    async def mark_child_result_patch_aggregated(
        self,
        child_run_id: UUID,
    ) -> TeamChildResult:
        async with self._sessions.begin() as session:
            record = await session.get(TeamChildResultRecord, child_run_id)
            if record is None:
                raise KeyError(child_run_id)
            record.patch_aggregated = True
            from awesome_agent.domain.models import utc_now

            record.updated_at = utc_now()
            return _child_result_from_record(record)

    async def get_mailbox_message(self, message_id: UUID) -> MailboxMessage:
        async with self._sessions() as session:
            record = await session.get(TeamMailboxMessageRecord, message_id)
        if record is None:
            raise KeyError(message_id)
        return _mailbox_from_record(record)

    async def mark_mailbox_read(self, message_id: UUID) -> MailboxMessage:
        from awesome_agent.domain.models import utc_now

        async with self._sessions.begin() as session:
            record = await session.get(TeamMailboxMessageRecord, message_id)
            if record is None:
                raise KeyError(message_id)
            record.status = MailboxMessageStatus.READ.value
            record.read_at = utc_now()
            return _mailbox_from_record(record)

    async def respond_to_mailbox_message(
        self,
        message_id: UUID,
        response: MailboxMessage,
    ) -> tuple[MailboxMessage, MailboxMessage]:
        from awesome_agent.domain.models import utc_now

        async with self._sessions.begin() as session:
            record = await session.get(TeamMailboxMessageRecord, message_id)
            if record is None:
                raise KeyError(message_id)
            record.status = MailboxMessageStatus.RESPONDED.value
            record.responded_at = utc_now()
            response = response.model_copy(
                update={"response_to_message_id": message_id}
            )
            session.add(_mailbox_to_record(response))
            return _mailbox_from_record(record), response

    async def list_mailbox_messages(
        self,
        run_id: UUID,
        *,
        include_archived: bool = False,
    ) -> list[MailboxMessage]:
        statement = select(TeamMailboxMessageRecord).where(
            (TeamMailboxMessageRecord.sender_run_id == run_id)
            | (TeamMailboxMessageRecord.recipient_run_id == run_id)
        )
        if not include_archived:
            statement = statement.where(
                TeamMailboxMessageRecord.status != MailboxMessageStatus.ARCHIVED.value
            )
        statement = statement.order_by(
            TeamMailboxMessageRecord.created_at,
            TeamMailboxMessageRecord.id,
        )
        async with self._sessions() as session:
            records = list(await session.scalars(statement))
        return [_mailbox_from_record(record) for record in records]


def _assignment_to_record(assignment: TeamAssignment) -> TeamAssignmentRecord:
    return TeamAssignmentRecord(
        id=assignment.id,
        root_run_id=assignment.root_run_id,
        parent_run_id=assignment.parent_run_id,
        child_run_id=assignment.child_run_id,
        kind=assignment.kind.value,
        status=assignment.status.value,
        role_profile=assignment.role_profile,
        graph_name=assignment.graph_name,
        graph_version=assignment.graph_version,
        goal=assignment.goal,
        allowed_tools=assignment.allowed_tools,
        deferred_tools=assignment.deferred_tools,
        promoted_tools=assignment.promoted_tools,
        allowed_skills=assignment.allowed_skills,
        can_write=assignment.can_write,
        can_delegate=assignment.can_delegate,
        max_subagents=assignment.max_subagents,
        acceptance_criteria=assignment.acceptance_criteria,
        handoff_context=assignment.handoff_context,
        retire_reason=assignment.retire_reason,
        created_at=assignment.created_at,
        updated_at=assignment.updated_at,
    )


def _assignment_from_record(record: TeamAssignmentRecord) -> TeamAssignment:
    return TeamAssignment(
        id=record.id,
        root_run_id=record.root_run_id,
        parent_run_id=record.parent_run_id,
        child_run_id=record.child_run_id,
        kind=TeamAssignmentKind(record.kind),
        status=TeamAssignmentStatus(record.status),
        role_profile=record.role_profile,
        graph_name=record.graph_name,
        graph_version=record.graph_version,
        goal=record.goal,
        allowed_tools=list(record.allowed_tools),
        deferred_tools=list(record.deferred_tools),
        promoted_tools=list(record.promoted_tools),
        allowed_skills=list(record.allowed_skills),
        can_write=record.can_write,
        can_delegate=record.can_delegate,
        max_subagents=record.max_subagents,
        acceptance_criteria=list(record.acceptance_criteria),
        handoff_context=dict(record.handoff_context),
        retire_reason=record.retire_reason,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _child_result_to_record(result: TeamChildResult) -> TeamChildResultRecord:
    return TeamChildResultRecord(
        child_run_id=result.child_run_id,
        assignment_id=result.assignment_id,
        parent_run_id=result.parent_run_id,
        root_run_id=result.root_run_id,
        status=result.status,
        summary=result.summary,
        patch_artifact_id=result.patch_artifact_id,
        changed_files=result.changed_files,
        evidence_artifact_refs=[str(value) for value in result.evidence_artifact_refs],
        failure_kind=result.failure_kind,
        patch_aggregated=result.patch_aggregated,
        created_at=result.created_at,
        updated_at=result.updated_at,
    )


def _child_result_from_record(record: TeamChildResultRecord) -> TeamChildResult:
    return TeamChildResult(
        assignment_id=record.assignment_id,
        child_run_id=record.child_run_id,
        parent_run_id=record.parent_run_id,
        root_run_id=record.root_run_id,
        status=record.status,  # type: ignore[arg-type]
        summary=record.summary,
        patch_artifact_id=record.patch_artifact_id,
        changed_files=list(record.changed_files),
        evidence_artifact_refs=[UUID(value) for value in record.evidence_artifact_refs],
        failure_kind=record.failure_kind,
        patch_aggregated=record.patch_aggregated,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _mailbox_to_record(message: MailboxMessage) -> TeamMailboxMessageRecord:
    return TeamMailboxMessageRecord(
        id=message.id,
        team_root_run_id=message.team_root_run_id,
        sender_run_id=message.sender_run_id,
        sender_agent_id=message.sender_agent_id,
        recipient_run_id=message.recipient_run_id,
        recipient_agent_id=message.recipient_agent_id,
        route=message.route.value,
        message_type=message.message_type.value,
        status=message.status.value,
        subject=message.subject,
        body_summary=message.body_summary,
        artifact_refs=[str(value) for value in message.artifact_refs],
        requires_response=message.requires_response,
        response_to_message_id=message.response_to_message_id,
        created_at=message.created_at,
        read_at=message.read_at,
        responded_at=message.responded_at,
    )


def _mailbox_from_record(record: TeamMailboxMessageRecord) -> MailboxMessage:
    return MailboxMessage(
        id=record.id,
        team_root_run_id=record.team_root_run_id,
        sender_run_id=record.sender_run_id,
        sender_agent_id=record.sender_agent_id,
        recipient_run_id=record.recipient_run_id,
        recipient_agent_id=record.recipient_agent_id,
        route=MailboxRoute(record.route),
        message_type=MailboxMessageType(record.message_type),
        status=MailboxMessageStatus(record.status),
        subject=record.subject,
        body_summary=record.body_summary,
        artifact_refs=[UUID(value) for value in record.artifact_refs],
        requires_response=record.requires_response,
        response_to_message_id=record.response_to_message_id,
        created_at=record.created_at,
        read_at=record.read_at,
        responded_at=record.responded_at,
    )
