from awesome_agent.domain.enums import TodoStatus

_TODO_TRANSITIONS: dict[TodoStatus, frozenset[TodoStatus]] = {
    TodoStatus.TODO: frozenset({TodoStatus.READY, TodoStatus.CANCELLED}),
    TodoStatus.READY: frozenset(
        {TodoStatus.IN_PROGRESS, TodoStatus.BLOCKED, TodoStatus.CANCELLED}
    ),
    TodoStatus.IN_PROGRESS: frozenset(
        {TodoStatus.BLOCKED, TodoStatus.SUBMITTED, TodoStatus.CANCELLED}
    ),
    TodoStatus.BLOCKED: frozenset(
        {TodoStatus.READY, TodoStatus.IN_PROGRESS, TodoStatus.CANCELLED}
    ),
    TodoStatus.SUBMITTED: frozenset(
        {TodoStatus.VERIFYING, TodoStatus.REJECTED, TodoStatus.CANCELLED}
    ),
    TodoStatus.VERIFYING: frozenset(
        {TodoStatus.REJECTED, TodoStatus.VERIFIED, TodoStatus.CANCELLED}
    ),
    TodoStatus.REJECTED: frozenset({TodoStatus.IN_PROGRESS, TodoStatus.CANCELLED}),
    TodoStatus.VERIFIED: frozenset({TodoStatus.DONE, TodoStatus.CANCELLED}),
    TodoStatus.DONE: frozenset(),
    TodoStatus.CANCELLED: frozenset(),
}


def can_transition_todo(current: TodoStatus, target: TodoStatus) -> bool:
    return target in _TODO_TRANSITIONS[current]


def ensure_todo_transition(current: TodoStatus, target: TodoStatus) -> None:
    if not can_transition_todo(current, target):
        raise ValueError(f"Invalid todo transition: {current} -> {target}")
