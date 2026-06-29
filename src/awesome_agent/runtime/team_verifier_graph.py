from __future__ import annotations

from datetime import UTC, datetime
from typing import NotRequired, TypedDict
from uuid import UUID

from awesome_agent.artifacts.repository import ArtifactMetadataRepository
from awesome_agent.artifacts.store import LocalArtifactStore
from awesome_agent.domain.models import Agent, Run
from awesome_agent.observability.facade import ObservabilityFacade
from awesome_agent.persistence.budget import BudgetRepository
from awesome_agent.persistence.team import TeamRepository
from awesome_agent.runtime.agent_loop import TeamAgentLoop
from awesome_agent.runtime.agent_loop.team_middleware import (
    ProviderResolver,
    TeamVerificationMiddleware,
    TeamVerifierInvalidOutput,
)
from awesome_agent.runtime.budget import BudgetPolicy
from awesome_agent.runtime.dispatch import PermanentExecutionError
from awesome_agent.runtime.repository import RuntimeRepository
from awesome_agent.runtime.team_assignments import (
    TeamAssignment,
    TeamAssignmentKind,
    TeamChildResult,
)
from awesome_agent.runtime.team_budget import ensure_team_budget
from awesome_agent.runtime.team_context import compact_team_payload
from awesome_agent.runtime.team_mailbox import (
    MailboxMessage,
    MailboxMessageType,
    MailboxRoute,
)
from awesome_agent.runtime.team_rework import (
    effective_child_results_for_verification,
    encode_rework_decision,
)
from awesome_agent.runtime.team_verification import TeamVerificationDecision

_TEAM_INLINE_PAYLOAD_TOKENS = 1200


class TeamVerifierState(TypedDict):
    run_id: str
    agent_id: str
    runtime_route: str
    phase: str
    result_summary: str
    final_answer: NotRequired[str]


class TeamVerifierGraph:
    def __init__(
        self,
        *,
        team_repository: TeamRepository,
        provider_resolver: ProviderResolver | None = None,
        artifact_store: LocalArtifactStore | None = None,
        artifact_repository: ArtifactMetadataRepository | None = None,
        budget_repository: BudgetRepository | None = None,
        budget_policy: BudgetPolicy | None = None,
        team_loop: TeamAgentLoop | None = None,
        observability: ObservabilityFacade | None = None,
    ) -> None:
        self.team_repository = team_repository
        self.provider_resolver = provider_resolver
        self.artifact_store = artifact_store
        self.artifact_repository = artifact_repository
        self.budget_repository = budget_repository
        self.budget_policy = budget_policy
        self.team_loop = team_loop or TeamAgentLoop(observability=observability)
        self.team_verification = TeamVerificationMiddleware(
            provider_resolver=provider_resolver,
            team_loop=self.team_loop,
        )

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
        await ensure_team_budget(
            run=run,
            repository=repository,
            budget_repository=self.budget_repository,
            policy=self.budget_policy,
            now=datetime.now(UTC),
            event_sink=event_sink,
            assignment=assignment,
            agent_id=agent.id,
        )
        assignments = await self.team_repository.list_assignments(
            assignment.root_run_id,
            include_inactive=True,
        )
        teammate_child_ids = {
            item.child_run_id
            for item in assignments
            if item.parent_run_id == assignment.parent_run_id
            and item.kind is TeamAssignmentKind.TEAMMATE
        }
        sibling_results = [
            result
            for result in await self.team_repository.list_child_results(
                assignment.parent_run_id
            )
            if result.child_run_id in teammate_child_ids
        ]
        sibling_results = effective_child_results_for_verification(
            sibling_results,
            assignments,
        )
        decision = await self._model_decision(
            run,
            agent,
            assignment=assignment,
            sibling_results=sibling_results,
            event_sink=event_sink,
        )
        await self._validate_decision(decision, assignment, sibling_results)
        passed = decision.decision == "passed"
        summary = (
            decision.summary
            if passed
            else f"Verifier {decision.decision}: {decision.summary}"
        )
        _artifact_refs, summary = await self._persist_result(
            run,
            agent,
            assignment,
            decision=decision,
            summary=summary,
        )
        if not passed:
            raise PermanentExecutionError(f"team_verification_{decision.decision}")
        return (
            TeamVerifierState(
                run_id=str(run.id),
                agent_id=str(agent.id),
                runtime_route=run.runtime_route or "team-verifier",
                phase=("passed" if passed else "rejected"),
                result_summary=summary,
                final_answer=summary,
            ),
            False,
        )

    async def _model_decision(
        self,
        run: Run,
        agent: Agent,
        *,
        assignment: TeamAssignment,
        sibling_results: list[TeamChildResult],
        event_sink: object | None,
    ) -> TeamVerificationDecision:
        try:
            return await self.team_verification.model_decision(
                run,
                agent,
                assignment=assignment,
                sibling_results=sibling_results,
                event_sink=event_sink,
            )
        except TeamVerifierInvalidOutput as error:
            await self._persist_invalid_output(run, agent, assignment, error.error)
            raise PermanentExecutionError("team_verifier_invalid_output") from error

    async def _validate_decision(
        self,
        decision: TeamVerificationDecision,
        assignment: TeamAssignment,
        sibling_results: list[TeamChildResult],
    ) -> None:
        sibling_ids = {str(result.child_run_id) for result in sibling_results}
        teammate_ids = {
            str(item.child_run_id)
            for item in await self.team_repository.list_assignments(
                assignment.root_run_id,
                include_inactive=True,
            )
            if item.parent_run_id == assignment.parent_run_id
            and item.kind is TeamAssignmentKind.TEAMMATE
        }
        if decision.decision == "passed":
            if not sibling_results:
                raise PermanentExecutionError("team_verification_no_child_results")
            if any(result.status != "completed" for result in sibling_results):
                raise PermanentExecutionError("team_verification_incomplete_children")
            if any(
                result.patch_artifact_id is not None and not result.patch_aggregated
                for result in sibling_results
            ):
                raise PermanentExecutionError("team_verification_unaggregated_patch")
        if decision.decision == "rework_required":
            for request in decision.rework_requests:
                if request.target_child_run_id not in sibling_ids:
                    raise PermanentExecutionError(
                        "team_verification_unknown_rework_target"
                    )
                if request.target_child_run_id not in teammate_ids:
                    raise PermanentExecutionError(
                        "team_verification_invalid_rework_target"
                    )

    async def _persist_invalid_output(
        self,
        run: Run,
        agent: Agent,
        assignment: TeamAssignment,
        error: str,
    ) -> None:
        decision = TeamVerificationDecision(
            decision="failed",
            summary=f"Verifier returned invalid output: {error}",
            failure_kind="model_output_failure",
        )
        await self._persist_result(
            run,
            agent,
            assignment,
            decision=decision,
            summary=decision.summary,
        )

    async def _persist_result(
        self,
        run: Run,
        agent: Agent,
        assignment: TeamAssignment,
        *,
        decision: TeamVerificationDecision,
        summary: str,
    ) -> tuple[list[UUID], str]:
        passed = decision.decision == "passed"
        if decision.decision == "rework_required":
            summary = encode_rework_decision(decision)
        compacted_summary = await compact_team_payload(
            run_id=run.id,
            agent_id=agent.id,
            runtime_route=run.runtime_route or "team-verifier",
            payload_kind="verifier-result",
            payload={
                "summary": summary,
                "decision": decision.model_dump(mode="json"),
            },
            artifact_store=self.artifact_store,
            artifact_repository=self.artifact_repository,
            budget_repository=self.budget_repository,
            max_inline_tokens=_TEAM_INLINE_PAYLOAD_TOKENS,
        )
        artifact_refs = compacted_summary.artifact_refs
        if compacted_summary.compacted:
            summary = compacted_summary.inline_payload["summary"]
        await self.team_repository.record_child_result(
            TeamChildResult(
                assignment_id=assignment.id,
                child_run_id=run.id,
                parent_run_id=assignment.parent_run_id,
                root_run_id=assignment.root_run_id,
                status=("completed" if passed else "failed"),
                summary=summary,
                evidence_artifact_refs=artifact_refs,
                failure_kind=(
                    None if passed else decision.failure_kind or decision.decision
                ),
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
                artifact_refs=artifact_refs,
                requires_response=not passed,
            )
        )
        return artifact_refs, summary


def verifier_model_rejection_budget() -> int:
    return 10


def verifier_external_retry_budget() -> int:
    return 1
