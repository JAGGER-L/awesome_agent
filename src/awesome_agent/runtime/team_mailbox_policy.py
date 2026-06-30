from __future__ import annotations

from awesome_agent.domain.models import Run
from awesome_agent.runtime.team_assignments import (
    TeamAssignment,
    TeamAssignmentKind,
)
from awesome_agent.runtime.team_mailbox import (
    MailboxMessageType,
    MailboxRoute,
    MailboxRouteError,
)

TEAM_MAILBOX_LIST_TOOL = "team.mailbox_list"
TEAM_MAILBOX_SEND_TOOL = "team.mailbox_send"
TEAM_MAILBOX_TOOLS = {TEAM_MAILBOX_LIST_TOOL, TEAM_MAILBOX_SEND_TOOL}

TEAMMATE_MAILBOX_MESSAGE_TYPES = {
    MailboxMessageType.QUESTION,
    MailboxMessageType.STATUS,
}


class MailboxPolicyError(MailboxRouteError):
    pass


def resolve_teammate_mailbox_route(
    *,
    sender_run: Run,
    sender_assignment: TeamAssignment,
    recipient_run: Run,
    message_type: MailboxMessageType,
) -> MailboxRoute:
    if sender_assignment.kind is not TeamAssignmentKind.TEAMMATE:
        raise MailboxPolicyError("only teammates can use the team mailbox")
    if sender_run.depth != 1 or sender_run.child_role != "teammate":
        raise MailboxPolicyError("only depth-1 teammates can use the team mailbox")
    if sender_assignment.child_run_id != sender_run.id:
        raise MailboxPolicyError("mailbox sender does not match assignment child")
    if message_type not in TEAMMATE_MAILBOX_MESSAGE_TYPES:
        raise MailboxPolicyError(
            "teammate mailbox tool supports only question and status message types"
        )

    root_run_id = sender_run.root_run_id or sender_assignment.root_run_id
    if recipient_run.id == sender_assignment.root_run_id:
        return MailboxRoute.TEAMMATE_TO_LEADER

    same_root = recipient_run.root_run_id == root_run_id
    sibling_teammate = (
        recipient_run.parent_run_id == sender_assignment.root_run_id
        and recipient_run.depth == 1
        and recipient_run.child_role == "teammate"
        and recipient_run.id != sender_run.id
    )
    if same_root and sibling_teammate:
        return MailboxRoute.TEAMMATE_TO_TEAMMATE

    raise MailboxPolicyError(
        "recipient must be the Leader root Run or a sibling Teammate Run"
    )
