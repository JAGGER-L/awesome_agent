from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from awesome_agent.domain.enums import TodoStatus
from awesome_agent.domain.models import TodoItem
from awesome_agent.domain.transitions import ensure_todo_transition


class TaskBoard:
    def __init__(self, *, run_id: UUID) -> None:
        self.run_id = run_id
        self._tasks: dict[UUID, TodoItem] = {}
        self._history: list[TodoItem] = []

    def add(self, task: TodoItem) -> None:
        if task.run_id != self.run_id:
            raise ValueError("Task belongs to a different run.")
        if task.id in self._tasks:
            raise ValueError("Task already exists.")
        self._tasks[task.id] = task
        self._record(task)

    def get(self, task_id: UUID) -> TodoItem:
        return self._tasks[task_id]

    def list_tasks(self) -> list[TodoItem]:
        return list(self._tasks.values())

    def transition(
        self,
        task_id: UUID,
        target: TodoStatus,
        *,
        blocker: str | None = None,
    ) -> TodoItem:
        current = self._tasks[task_id]
        ensure_todo_transition(current.status, target)
        updated = current.model_copy(
            update={
                "status": target,
                "blocker": blocker,
                "revision": current.revision + 1,
                "updated_at": datetime.now(UTC),
            }
        )
        self._tasks[task_id] = updated
        self._record(updated)
        return updated

    def history(self, task_id: UUID) -> list[TodoItem]:
        return [item for item in self._history if item.id == task_id]

    def _record(self, task: TodoItem) -> None:
        self._history.append(task.model_copy(deep=True))
