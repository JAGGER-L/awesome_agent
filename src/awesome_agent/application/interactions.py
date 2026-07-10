from __future__ import annotations

import asyncio
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, ConfigDict


class InteractionKind(StrEnum):
    WORKSPACE_TRUST = "workspace_trust"
    EXECUTE_BOUNDARY = "execute_boundary"


class InteractionDecision(StrEnum):
    TRUST = "trust"
    ALLOW_ONCE = "allow_once"
    DENY = "deny"


class PendingInteraction(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    kind: InteractionKind
    prompt: str
    choices: tuple[InteractionDecision, ...]
    scope: str | None


class InteractionBusy(RuntimeError):
    pass


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
        choices: tuple[InteractionDecision, ...],
        scope: str | None,
    ) -> PendingInteraction:
        if self.pending is not None:
            raise InteractionBusy("Another interaction is pending.")
        pending = PendingInteraction(
            id=f"interaction_{uuid4().hex}",
            kind=kind,
            prompt=prompt,
            choices=choices,
            scope=scope,
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
            or decision not in pending.choices
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
