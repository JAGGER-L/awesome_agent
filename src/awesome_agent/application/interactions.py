from __future__ import annotations

import asyncio
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, ConfigDict


class InteractionKind(StrEnum):
    WORKSPACE_TRUST = "workspace_trust"
    TOOL_APPROVAL = "tool_approval"
    FULL_ACCESS_CONFIRMATION = "full_access_confirmation"


class InteractionDecision(StrEnum):
    TRUST = "trust"
    ALLOW_ONCE = "allow_once"
    ALLOW_THREAD_WRITES = "allow_thread_writes"
    ENABLE_FULL_ACCESS = "enable_full_access"
    DENY = "deny"


class InteractionChoice(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: InteractionDecision
    label: str
    description: str | None = None


class PendingInteraction(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    kind: InteractionKind
    prompt: str
    operation: str
    target: str
    capability: str | None
    choices: tuple[InteractionChoice, ...]


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


def tool_approval_choices(capability: str) -> tuple[InteractionChoice, ...]:
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
            decision=InteractionDecision.ENABLE_FULL_ACCESS,
            label="Enable Full access for this thread",
            description=(
                "Awesome will edit files and run shell commands without approval."
            ),
        ),
        InteractionChoice(
            decision=InteractionDecision.DENY,
            label="Keep Request approval",
        ),
    )


class InteractionCoordinator:
    def __init__(self) -> None:
        self.pending: PendingInteraction | None = None
        self._future: asyncio.Future[InteractionDecision] | None = None
        self._resolved: InteractionDecision | None = None

    def create(
        self,
        *,
        kind: InteractionKind,
        prompt: str,
        operation: str,
        target: str,
        capability: str | None,
        choices: tuple[InteractionChoice, ...],
    ) -> PendingInteraction:
        if self.pending is not None:
            raise InteractionBusy("Another interaction is pending.")
        pending = PendingInteraction(
            id=f"interaction_{uuid4().hex}",
            kind=kind,
            prompt=prompt,
            operation=operation,
            target=target,
            capability=capability,
            choices=choices,
        )
        self.pending = pending
        self._future = None
        self._resolved = None
        return pending

    def resolve(
        self,
        interaction_id: str,
        decision: InteractionDecision,
    ) -> bool:
        pending = self.pending
        if (
            pending is None
            or pending.id != interaction_id
            or decision not in {choice.decision for choice in pending.choices}
            or self._resolved is not None
        ):
            return False
        if self._future is not None:
            self._future.set_result(decision)
        else:
            self._resolved = decision
        return True

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
