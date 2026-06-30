from uuid import UUID, uuid4

import pytest

from awesome_agent.domain.models import Run
from awesome_agent.runtime.team_assignments import (
    TeamAssignment,
    TeamAssignmentKind,
)
from awesome_agent.runtime.team_mailbox import MailboxMessageType, MailboxRoute
from awesome_agent.runtime.team_mailbox_policy import (
    MailboxPolicyError,
    resolve_teammate_mailbox_route,
)


def test_teammate_can_message_leader() -> None:
    root = _run(depth=0, child_role=None)
    teammate = _run(
        root_run_id=root.id,
        parent_run_id=root.id,
        depth=1,
        child_role="teammate",
    )
    assignment = _assignment(root, teammate, TeamAssignmentKind.TEAMMATE)

    route = resolve_teammate_mailbox_route(
        sender_run=teammate,
        sender_assignment=assignment,
        recipient_run=root,
        message_type=MailboxMessageType.QUESTION,
    )

    assert route is MailboxRoute.TEAMMATE_TO_LEADER


def test_teammate_can_message_sibling_teammate() -> None:
    root = _run(depth=0, child_role=None)
    sender = _run(
        root_run_id=root.id,
        parent_run_id=root.id,
        depth=1,
        child_role="teammate",
    )
    recipient = _run(
        root_run_id=root.id,
        parent_run_id=root.id,
        depth=1,
        child_role="teammate",
    )
    assignment = _assignment(root, sender, TeamAssignmentKind.TEAMMATE)

    route = resolve_teammate_mailbox_route(
        sender_run=sender,
        sender_assignment=assignment,
        recipient_run=recipient,
        message_type=MailboxMessageType.STATUS,
    )

    assert route is MailboxRoute.TEAMMATE_TO_TEAMMATE


def test_subagent_cannot_use_team_mailbox() -> None:
    root = _run(depth=0, child_role=None)
    teammate = _run(
        root_run_id=root.id,
        parent_run_id=root.id,
        depth=1,
        child_role="teammate",
    )
    subagent = _run(
        root_run_id=root.id,
        parent_run_id=teammate.id,
        depth=2,
        child_role="subagent",
    )
    assignment = _assignment(root, subagent, TeamAssignmentKind.SUBAGENT)

    with pytest.raises(MailboxPolicyError, match="only teammates"):
        resolve_teammate_mailbox_route(
            sender_run=subagent,
            sender_assignment=assignment,
            recipient_run=teammate,
            message_type=MailboxMessageType.QUESTION,
        )


def test_teammate_cannot_message_verifier_or_subagent() -> None:
    root = _run(depth=0, child_role=None)
    teammate = _run(
        root_run_id=root.id,
        parent_run_id=root.id,
        depth=1,
        child_role="teammate",
    )
    verifier = _run(
        root_run_id=root.id,
        parent_run_id=root.id,
        depth=1,
        child_role="verifier",
    )
    subagent = _run(
        root_run_id=root.id,
        parent_run_id=teammate.id,
        depth=2,
        child_role="subagent",
    )
    assignment = _assignment(root, teammate, TeamAssignmentKind.TEAMMATE)

    for recipient in (verifier, subagent):
        with pytest.raises(MailboxPolicyError, match="recipient"):
            resolve_teammate_mailbox_route(
                sender_run=teammate,
                sender_assignment=assignment,
                recipient_run=recipient,
                message_type=MailboxMessageType.QUESTION,
            )


def test_teammate_mailbox_tool_rejects_formal_result_and_rework_types() -> None:
    root = _run(depth=0, child_role=None)
    teammate = _run(
        root_run_id=root.id,
        parent_run_id=root.id,
        depth=1,
        child_role="teammate",
    )
    assignment = _assignment(root, teammate, TeamAssignmentKind.TEAMMATE)

    for message_type in (
        MailboxMessageType.ASSIGNMENT,
        MailboxMessageType.RESULT,
        MailboxMessageType.VERIFICATION,
        MailboxMessageType.REWORK,
    ):
        with pytest.raises(MailboxPolicyError, match="message type"):
            resolve_teammate_mailbox_route(
                sender_run=teammate,
                sender_assignment=assignment,
                recipient_run=root,
                message_type=message_type,
            )


def _run(
    *,
    root_run_id: UUID | None = None,
    parent_run_id: UUID | None = None,
    depth: int,
    child_role: str | None,
) -> Run:
    return Run(
        id=uuid4(),
        goal=f"run depth {depth}",
        root_run_id=root_run_id,
        parent_run_id=parent_run_id,
        depth=depth,
        child_role=child_role,
    )


def _assignment(
    root: Run,
    child: Run,
    kind: TeamAssignmentKind,
) -> TeamAssignment:
    return TeamAssignment(
        root_run_id=root.id,
        parent_run_id=child.parent_run_id or root.id,
        child_run_id=child.id,
        kind=kind,
        role_profile=child.child_role or "leader",
        runtime_route="team-role",
        goal=child.goal,
    )
