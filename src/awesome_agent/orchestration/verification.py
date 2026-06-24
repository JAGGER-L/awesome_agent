from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from awesome_agent.domain.enums import AgentKind, TodoStatus
from awesome_agent.orchestration.tasks import TaskBoard
from awesome_agent.orchestration.team import TeamRuntime


class VerificationCheck(BaseModel):
    name: str
    passed: bool
    evidence: str


class VerificationReport(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    task_id: UUID
    verifier_id: UUID
    passed: bool
    summary: str
    checks: list[VerificationCheck] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class VerificationCoordinator:
    def __init__(self, *, team: TeamRuntime, tasks: TaskBoard) -> None:
        self._team = team
        self._tasks = tasks
        self._reports: list[VerificationReport] = []

    def submit(self, task_id: UUID, *, teammate_id: UUID) -> None:
        task = self._tasks.get(task_id)
        if task.primary_owner_id != teammate_id:
            raise PermissionError("Only the primary owner may submit a task.")
        self._tasks.transition(task_id, TodoStatus.SUBMITTED)

    def begin(self, task_id: UUID, *, verifier_id: UUID) -> None:
        self._ensure_verifier(verifier_id)
        self._tasks.transition(task_id, TodoStatus.VERIFYING)

    def decide(self, report: VerificationReport) -> TodoStatus:
        self._ensure_verifier(report.verifier_id)
        task = self._tasks.get(report.task_id)
        if task.status is not TodoStatus.VERIFYING:
            raise ValueError("Task must be VERIFYING before a decision.")
        self._reports.append(report)
        target = TodoStatus.VERIFIED if report.passed else TodoStatus.REJECTED
        self._tasks.transition(report.task_id, target)
        return target

    def complete(self, task_id: UUID, *, leader_id: UUID) -> None:
        if self._team.leader.id != leader_id:
            raise PermissionError("Only the Leader may complete a task.")
        self._tasks.transition(task_id, TodoStatus.DONE)

    def reports_for(self, task_id: UUID) -> list[VerificationReport]:
        return [report for report in self._reports if report.task_id == task_id]

    def _ensure_verifier(self, agent_id: UUID) -> None:
        handle = self._team.teammates.get(agent_id)
        if handle is None or handle.session.agent.kind is not AgentKind.VERIFIER:
            raise PermissionError("Only the team Verifier may perform verification.")
