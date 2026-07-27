from __future__ import annotations

import asyncio
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class InteractionKind(StrEnum):
    WORKSPACE_TRUST = "workspace_trust"
    STATE_RESET = "state_reset"
    TOOL_APPROVAL = "tool_approval"
    FULL_ACCESS_CONFIRMATION = "full_access_confirmation"
    RECOVERY_DECISION = "recovery_decision"


class InteractionDecision(StrEnum):
    TRUST = "trust"
    RESET_STATE = "reset_state"
    ALLOW_ONCE = "allow_once"
    ALLOW_THREAD_WRITES = "allow_thread_writes"
    ALLOW_THREAD_NETWORK = "allow_thread_network"
    ENABLE_FULL_ACCESS = "enable_full_access"
    RETRY = "retry"
    ABORT = "abort"
    DENY = "deny"


class InteractionChoice(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: InteractionDecision
    label: str
    description: str | None = None


class PendingInteraction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    kind: InteractionKind
    prompt: str
    operation: str
    target: str
    capability: str | None
    choices: tuple[InteractionChoice, ...]
    generation: int = Field(ge=1)
    thread_id: str | None = Field(default=None, max_length=128)
    turn_id: str | None = Field(default=None, max_length=128)
    operation_id: str | None = Field(default=None, max_length=128)
    permission_generation: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_authority_binding(self) -> PendingInteraction:
        if self.kind is InteractionKind.FULL_ACCESS_CONFIRMATION and (
            self.thread_id is None or self.permission_generation is None
        ):
            raise ValueError("Full access confirmation requires thread authority.")
        if self.kind is InteractionKind.TOOL_APPROVAL and (
            self.thread_id is None or self.turn_id is None or self.operation_id is None
        ):
            raise ValueError("Tool approval requires operation authority.")
        if self.kind is InteractionKind.RECOVERY_DECISION and (
            self.thread_id is None or self.turn_id is None
        ):
            raise ValueError("Recovery decision requires Turn authority.")
        return self


class InteractionBusy(RuntimeError):
    pass


def workspace_trust_choices() -> tuple[InteractionChoice, ...]:
    return (
        InteractionChoice(
            decision=InteractionDecision.TRUST,
            label="Yes, I trust this folder",
        ),
        InteractionChoice(decision=InteractionDecision.DENY, label="No, exit"),
    )


def state_reset_choices() -> tuple[InteractionChoice, ...]:
    return (
        InteractionChoice(
            decision=InteractionDecision.RESET_STATE,
            label="Reset local state and continue",
        ),
        InteractionChoice(decision=InteractionDecision.DENY, label="Exit"),
    )


def tool_approval_choices(capability: str) -> tuple[InteractionChoice, ...]:
    if capability == "network.read":
        return (
            InteractionChoice(
                decision=InteractionDecision.DENY,
                label="Deny",
                description="Do not send this search query to Tavily.",
            ),
            InteractionChoice(
                decision=InteractionDecision.ALLOW_ONCE,
                label="Allow once",
                description="Send this search query to Tavily once.",
            ),
            InteractionChoice(
                decision=InteractionDecision.ALLOW_THREAD_NETWORK,
                label="Allow for this Thread",
                description="Allow Web search requests for this Thread.",
            ),
        )
    choices = [InteractionChoice(decision=InteractionDecision.ALLOW_ONCE, label="Yes")]
    if capability == "workspace.write":
        choices.append(
            InteractionChoice(
                decision=InteractionDecision.ALLOW_THREAD_WRITES,
                label="Yes, allow all edits during this session",
            )
        )
    choices.append(InteractionChoice(decision=InteractionDecision.DENY, label="No"))
    return tuple(choices)


def full_access_confirmation_choices() -> tuple[InteractionChoice, ...]:
    return (
        InteractionChoice(
            decision=InteractionDecision.DENY,
            label="Keep current permission mode",
        ),
        InteractionChoice(
            decision=InteractionDecision.ENABLE_FULL_ACCESS,
            label="Enable Full access for this thread",
            description=(
                "Awesome will edit files and run shell commands without approval."
            ),
        ),
    )


def recovery_decision_choices(
    *,
    uncertain: bool,
) -> tuple[InteractionChoice, ...]:
    retry_description = (
        "The external action may run again because its previous outcome is unknown."
        if uncertain
        else "Continue the unfinished Turn from its verified checkpoint."
    )
    retry = InteractionChoice(
        decision=InteractionDecision.RETRY,
        label="Retry",
        description=retry_description,
    )
    abort = InteractionChoice(
        decision=InteractionDecision.ABORT,
        label="Abort",
        description="Mark the unfinished Turn as failed without continuing it.",
    )
    return (abort, retry) if uncertain else (retry, abort)


class InteractionCoordinator:
    def __init__(self) -> None:
        self.pending: PendingInteraction | None = None
        self._future: asyncio.Future[InteractionDecision] | None = None
        self._resolved: InteractionDecision | None = None
        self._generation = 0

    def create(
        self,
        *,
        kind: InteractionKind,
        prompt: str,
        operation: str,
        target: str,
        capability: str | None,
        choices: tuple[InteractionChoice, ...],
        thread_id: str | None = None,
        turn_id: str | None = None,
        operation_id: str | None = None,
        permission_generation: int | None = None,
    ) -> PendingInteraction:
        if self.pending is not None:
            raise InteractionBusy("Another interaction is pending.")
        generation = self._generation + 1
        pending = PendingInteraction(
            id=f"interaction_{uuid4().hex}",
            kind=kind,
            prompt=prompt,
            operation=operation,
            target=target,
            capability=capability,
            choices=choices,
            generation=generation,
            thread_id=thread_id,
            turn_id=turn_id,
            operation_id=operation_id,
            permission_generation=permission_generation,
        )
        self._generation = generation
        self.pending = pending
        self._future = None
        self._resolved = None
        return pending

    def resolve(
        self,
        interaction_id: str,
        decision: InteractionDecision,
    ) -> bool:
        if not self.allows(interaction_id, decision) or self._resolved is not None:
            return False
        future = self._future
        if future is not None and future.done():
            return False
        self._resolved = decision
        if future is not None:
            future.set_result(decision)
        return True

    def allows(
        self,
        interaction_id: str,
        decision: InteractionDecision,
    ) -> bool:
        pending = self.pending
        return (
            pending is not None
            and pending.id == interaction_id
            and decision in {choice.decision for choice in pending.choices}
        )

    async def wait(self, interaction_id: str) -> InteractionDecision:
        pending = self.pending
        if pending is None or pending.id != interaction_id:
            raise LookupError(interaction_id)
        try:
            if self._resolved is not None:
                return self._resolved
            if self._future is None:
                self._future = asyncio.get_running_loop().create_future()
            return await self._future
        finally:
            if self.pending is not None and self.pending.id == interaction_id:
                self.pending = None
                self._future = None
                self._resolved = None

    def cancel_pending(self) -> bool:
        if self.pending is None:
            return False
        return self.resolve(self.pending.id, InteractionDecision.DENY)

    def discard(self, interaction_id: str) -> bool:
        pending = self.pending
        if pending is None or pending.id != interaction_id:
            return False
        future = self._future
        self.pending = None
        self._future = None
        self._resolved = None
        if future is not None and not future.done():
            future.cancel()
        return True
