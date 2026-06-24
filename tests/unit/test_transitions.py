import pytest

from awesome_agent.domain.enums import TodoStatus
from awesome_agent.domain.transitions import (
    can_transition_todo,
    ensure_todo_transition,
)


def test_verified_can_transition_to_done() -> None:
    assert can_transition_todo(TodoStatus.VERIFIED, TodoStatus.DONE)


def test_in_progress_cannot_skip_verification() -> None:
    assert not can_transition_todo(TodoStatus.IN_PROGRESS, TodoStatus.DONE)

    with pytest.raises(ValueError, match="Invalid todo transition"):
        ensure_todo_transition(TodoStatus.IN_PROGRESS, TodoStatus.DONE)
