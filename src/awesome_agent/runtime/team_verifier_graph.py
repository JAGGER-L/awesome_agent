from __future__ import annotations

from typing import NotRequired, TypedDict

from awesome_agent.domain.models import Agent, Run
from awesome_agent.persistence.team import TeamRepository
from awesome_agent.runtime.repository import RuntimeRepository
from awesome_agent.runtime.team_assignments import (
    TeamAssignmentKind,
    TeamChildResult,
)
from awesome_agent.runtime.team_mailbox import (
    MailboxMessage,
    MailboxMessageType,
    MailboxRoute,
)


class TeamVerifierState(TypedDict):
    run_id: str
    agent_id: str
    graph_name: str
    graph_version: int
    phase: str
    result_summary: str
    final_answer: NotRequired[str]


class TeamVerifierGraph:
    def __init__(self, *, team_repository: TeamRepository) -> None:
        self.team_repository = team_repository

    async def execute(
        self,
        run: Run,
        agent: Agent,
        *,
        repository: RuntimeRepository,
        event_sink: object | None = None,
    ) -> tuple[TeamVerifierState, bool]:
        assignment = await self.team_repository.get_assignment_for_child_run(run.id)
        if assignment.kind is not TeamAssignmentKind.VERIFIER:
            raise ValueError("team-verifier graph requires verifier assignment")
        sibling_results = [
            result
            for result in await self.team_repository.list_child_results(
                assignment.parent_run_id
            )
            if result.child_run_id != run.id
        ]
        passed = all(
            result.status == "completed"
            and (result.patch_artifact_id is None or result.patch_aggregated)
            for result in sibling_results
        )
        summary = (
            "Verifier passed aggregated child results."
            if passed
            else "Verifier rejected incomplete child aggregation."
        )
        status = "completed" if passed else "failed"
        await self.team_repository.record_child_result(
            TeamChildResult(
                assignment_id=assignment.id,
                child_run_id=run.id,
                parent_run_id=assignment.parent_run_id,
                root_run_id=assignment.root_run_id,
                status=status,  # type: ignore[arg-type]
                summary=summary,
                failure_kind=(None if passed else "model_output_failure"),
            )
        )
        await self.team_repository.create_mailbox_message(
            MailboxMessage(
                team_root_run_id=assignment.root_run_id,
                sender_run_id=run.id,
                sender_agent_id=agent.id,
                recipient_run_id=assignment.parent_run_id,
                recipient_agent_id=None,
                route=MailboxRoute.VERIFIER_TO_LEADER,
                message_type=MailboxMessageType.VERIFICATION,
                subject="Verifier result",
                body_summary=summary,
                requires_response=not passed,
            )
        )
        return (
            TeamVerifierState(
                run_id=str(run.id),
                agent_id=str(agent.id),
                graph_name=run.graph_name or "team-verifier",
                graph_version=run.graph_version or 1,
                phase=("passed" if passed else "rejected"),
                result_summary=summary,
                final_answer=summary,
            ),
            False,
        )


def verifier_model_rejection_budget() -> int:
    return 10


def verifier_external_retry_budget() -> int:
    return 1
