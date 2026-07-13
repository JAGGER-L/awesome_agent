from __future__ import annotations

from collections.abc import Callable

from awesome_agent.application.command_results import (
    CommandApplicationInteraction,
    CommandOption,
    CommandOutcome,
    CommandSelection,
    PermissionCommandPayload,
    error,
    interaction,
    result,
)
from awesome_agent.application.commands import CommandIntent
from awesome_agent.application.interactions import (
    InteractionCoordinator,
    InteractionKind,
    full_access_confirmation_choices,
)
from awesome_agent.application.operations import OperationController
from awesome_agent.core.events import (
    EventEmitter,
    InteractionChoicePayload,
    InteractionRequiredPayload,
)
from awesome_agent.core.tools.permissions import PermissionMode, PermissionSession


class PermissionCommandService:
    """Own session permission-mode queries and escalation interactions."""

    def __init__(
        self,
        *,
        session: PermissionSession,
        operations: OperationController,
        interactions: InteractionCoordinator,
        emitter: EventEmitter,
        current_thread_id: Callable[[], str | None],
    ) -> None:
        self._session = session
        self._operations = operations
        self._interactions = interactions
        self._emitter = emitter
        self._current_thread_id = current_thread_id

    async def permissions(self, intent: CommandIntent) -> CommandOutcome:
        if not intent.arguments:
            payload = PermissionCommandPayload(mode=self._session.mode)
            return interaction(
                CommandSelection(
                    prompt="Permission mode",
                    options=(
                        CommandOption(
                            value=PermissionMode.REQUEST_APPROVAL.value,
                            label="Request approval",
                            description=(
                                "Ask before edits, deletes, and shell commands."
                            ),
                            selected=self._session.mode
                            is PermissionMode.REQUEST_APPROVAL,
                        ),
                        CommandOption(
                            value=PermissionMode.FULL_ACCESS.value,
                            label="Full access",
                            description=(
                                "Allow edits and shell commands for this thread."
                            ),
                            selected=self._session.mode is PermissionMode.FULL_ACCESS,
                        ),
                    ),
                ),
                context=payload,
            )
        if len(intent.arguments) != 1:
            return error(
                "invalid_arguments",
                "Usage: /permissions [request_approval|full_access]",
            )
        try:
            requested = PermissionMode(intent.arguments[0])
        except ValueError:
            return error(
                "invalid_arguments",
                "Usage: /permissions [request_approval|full_access]",
            )
        if self._operations.active_operation_id is not None:
            return error(
                "operation_busy",
                "Permission mode cannot change during an active operation.",
            )
        if self._interactions.pending is not None:
            return error(
                "interaction_busy",
                "Resolve the pending interaction before changing permission mode.",
            )
        if requested is PermissionMode.REQUEST_APPROVAL:
            self._session.reset()
            return result(PermissionCommandPayload(mode=requested))
        if self._session.mode is PermissionMode.FULL_ACCESS:
            return result(PermissionCommandPayload(mode=requested))
        pending = self._interactions.create(
            kind=InteractionKind.FULL_ACCESS_CONFIRMATION,
            prompt="Enable Full access for this thread?",
            operation="enable",
            target="full access",
            capability=None,
            choices=full_access_confirmation_choices(),
        )
        await self._emitter.emit(
            InteractionRequiredPayload(
                interaction_id=pending.id,
                interaction_kind="full_access_confirmation",
                prompt=pending.prompt,
                operation=pending.operation,
                target=pending.target,
                capability=pending.capability,
                choices=tuple(
                    InteractionChoicePayload(
                        decision=choice.decision.value,
                        label=choice.label,
                        description=choice.description,
                    )
                    for choice in pending.choices
                ),
            ),
            thread_id=self._current_thread_id(),
        )
        return interaction(CommandApplicationInteraction(interaction_id=pending.id))
