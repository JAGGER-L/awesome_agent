from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from awesome_agent.application.contracts import (
    ApplicationResult,
    InitializeResult,
    InitializeStatus,
    InteractionResult,
)
from awesome_agent.application.interactions import InteractionDecision
from awesome_agent.application.middleware import ApplicationOperation


class BootstrapPhase(StrEnum):
    """Surface-visible readiness phases owned by one LocalApplication."""

    UNINITIALIZED = "uninitialized"
    INITIALIZING = "initializing"
    TRUST_REQUIRED = "trust_required"
    STATE_RESET_REQUIRED = "state_reset_required"
    READY = "ready"


@dataclass(frozen=True, slots=True)
class BootstrapRejection:
    """Safe, immutable reason a surface request is not yet admitted."""

    message: str
    diagnostic_code: str


@dataclass(frozen=True, slots=True)
class _StableBootstrapState:
    phase: BootstrapPhase
    interaction_id: str | None


@dataclass(frozen=True, slots=True)
class _InitializeTransition:
    generation: int | None
    origin: _StableBootstrapState


@dataclass(frozen=True, slots=True)
class _InteractionTransition:
    phase: BootstrapPhase
    interaction_id: str
    decision: InteractionDecision


@dataclass(frozen=True, slots=True)
class _PreInitializeTransition:
    generation: int
    operation: ApplicationOperation


_INITIALIZATION_IN_PROGRESS = BootstrapRejection(
    message="Server initialization is in progress",
    diagnostic_code="initialization_in_progress",
)
_SERVER_NOT_INITIALIZED = BootstrapRejection(
    message="Server not initialized",
    diagnostic_code="server_not_initialized",
)
_SERVER_NOT_READY = BootstrapRejection(
    message="Server not ready",
    diagnostic_code="server_not_ready",
)
_PREINITIALIZE_OPERATION_IN_PROGRESS = BootstrapRejection(
    message="A pre-initialize operation is in progress",
    diagnostic_code="preinitialize_operation_in_progress",
)
_SKILL_MANAGEMENT_REQUIRES_UNINITIALIZED = BootstrapRejection(
    message="Skill package management is only available before initialization",
    diagnostic_code="skill_management_requires_uninitialized",
)
_SKILL_MANAGEMENT_OPERATIONS = frozenset(
    {
        ApplicationOperation.SKILL_LIST,
        ApplicationOperation.SKILL_INSTALL,
        ApplicationOperation.SKILL_REMOVE,
    }
)
_URGENT_OPERATIONS = frozenset(
    {
        ApplicationOperation.CANCEL_OPERATION,
        ApplicationOperation.SHUTDOWN,
    }
)
_INTERACTION_PHASES = frozenset(
    {
        BootstrapPhase.TRUST_REQUIRED,
        BootstrapPhase.STATE_RESET_REQUIRED,
    }
)


class ApplicationBootstrap:
    """Concrete bootstrap state machine shared read-only with product surfaces."""

    def __init__(self) -> None:
        self._phase = BootstrapPhase.UNINITIALIZED
        self._interaction_id: str | None = None
        self._next_initialize_generation = 0
        self._active_initialize_generation: int | None = None
        self._next_preinitialize_generation = 0
        self._active_preinitialize: _PreInitializeTransition | None = None

    @property
    def phase(self) -> BootstrapPhase:
        return self._phase

    @property
    def interaction_id(self) -> str | None:
        return self._interaction_id

    @property
    def preinitialize_active(self) -> bool:
        return self._active_preinitialize is not None

    def rejection(
        self,
        operation: ApplicationOperation | None,
    ) -> BootstrapRejection | None:
        """Return a safe rejection, or None when a surface may dispatch."""

        if operation is not None and type(operation) is not ApplicationOperation:
            raise TypeError("Bootstrap admission requires an ApplicationOperation.")
        if operation in _SKILL_MANAGEMENT_OPERATIONS:
            if self._phase is not BootstrapPhase.UNINITIALIZED:
                return _SKILL_MANAGEMENT_REQUIRES_UNINITIALIZED
            if self._active_preinitialize is not None:
                return _PREINITIALIZE_OPERATION_IN_PROGRESS
            return None
        if (
            operation is ApplicationOperation.INITIALIZE
            and self._active_preinitialize is not None
        ):
            return _PREINITIALIZE_OPERATION_IN_PROGRESS
        if operation in _URGENT_OPERATIONS or self._phase is BootstrapPhase.READY:
            return None
        if operation is ApplicationOperation.INITIALIZE:
            return (
                _INITIALIZATION_IN_PROGRESS
                if self._phase is BootstrapPhase.INITIALIZING
                else None
            )
        if (
            operation is ApplicationOperation.RESPOND_INTERACTION
            and self._phase in _INTERACTION_PHASES
        ):
            return None
        if self._phase is BootstrapPhase.UNINITIALIZED:
            return _SERVER_NOT_INITIALIZED
        return _SERVER_NOT_READY

    def begin_initialize(self) -> _InitializeTransition:
        """Enter INITIALIZING synchronously and return an exact rollback token."""

        if self._active_preinitialize is not None:
            raise RuntimeError("A pre-initialize operation is already in progress.")
        if self._phase is BootstrapPhase.INITIALIZING:
            raise RuntimeError("Application initialization is already in progress.")
        origin = self._stable_state()
        if self._phase is BootstrapPhase.READY:
            return _InitializeTransition(generation=None, origin=origin)
        self._next_initialize_generation += 1
        generation = self._next_initialize_generation
        self._active_initialize_generation = generation
        self._phase = BootstrapPhase.INITIALIZING
        self._interaction_id = None
        return _InitializeTransition(generation=generation, origin=origin)

    def begin_preinitialize(
        self,
        operation: ApplicationOperation,
    ) -> _PreInitializeTransition:
        if operation not in _SKILL_MANAGEMENT_OPERATIONS:
            raise ValueError("Operation is not available before initialization.")
        rejection = self.rejection(operation)
        if rejection is not None:
            raise RuntimeError(rejection.message)
        self._next_preinitialize_generation += 1
        transition = _PreInitializeTransition(
            generation=self._next_preinitialize_generation,
            operation=operation,
        )
        self._active_preinitialize = transition
        return transition

    def complete_preinitialize(self, transition: _PreInitializeTransition) -> None:
        if type(transition) is not _PreInitializeTransition:
            raise TypeError("Invalid pre-initialize transition token.")
        if self._active_preinitialize != transition:
            raise RuntimeError("Pre-initialize transition is no longer active.")
        self._active_preinitialize = None

    def complete_initialize(
        self,
        transition: _InitializeTransition,
        result: ApplicationResult[InitializeResult],
    ) -> None:
        """Apply one typed initialize result or roll back a failed attempt."""

        self._require_initialize_transition(transition)
        try:
            state = _state_from_initialize_outcome(result)
            if state is None:
                self._restore_initialize(transition)
                return
            if transition.origin.phase is BootstrapPhase.READY and (
                state.phase is not BootstrapPhase.READY
            ):
                raise RuntimeError(
                    "A ready application cannot regress during initialize."
                )
        except BaseException:
            self._restore_initialize(transition)
            raise
        self._active_initialize_generation = None
        self._phase = state.phase
        self._interaction_id = state.interaction_id

    def abort_initialize(self, transition: _InitializeTransition) -> None:
        """Restore the exact stable phase after initialize raises or is cancelled."""

        self._require_initialize_transition(transition)
        self._restore_initialize(transition)

    def begin_interaction(
        self,
        interaction_id: str,
        decision: str,
    ) -> _InteractionTransition | None:
        """Bind a possible bootstrap resolution to the exact pending identity."""

        if (
            self._phase not in _INTERACTION_PHASES
            or self._interaction_id != interaction_id
        ):
            return None
        try:
            parsed = InteractionDecision(decision)
        except ValueError:
            return None
        return _InteractionTransition(
            phase=self._phase,
            interaction_id=interaction_id,
            decision=parsed,
        )

    def complete_interaction(
        self,
        transition: _InteractionTransition,
        result: ApplicationResult[InteractionResult],
    ) -> None:
        """Open readiness only for the exact accepted workspace-trust decision."""

        if type(transition) is not _InteractionTransition:
            raise TypeError("Invalid bootstrap interaction transition token.")
        if (
            self._phase is not transition.phase
            or self._interaction_id != transition.interaction_id
        ):
            return
        if not isinstance(result, ApplicationResult):
            raise TypeError("Interaction returned an invalid result contract.")
        if result.ok is not True:
            return
        if result.error is not None:
            raise ValueError("Successful interaction result forbids an error.")
        value = result.value
        if (
            type(value) is not InteractionResult
            or value.accepted is not True
            or value.status != "resolved"
            or value.error is not None
        ):
            return
        if (
            transition.phase is BootstrapPhase.TRUST_REQUIRED
            and transition.decision is InteractionDecision.TRUST
        ):
            self._phase = BootstrapPhase.READY
            self._interaction_id = None

    def _stable_state(self) -> _StableBootstrapState:
        if self._phase is BootstrapPhase.INITIALIZING:
            raise RuntimeError("Initializing is not a stable bootstrap phase.")
        return _StableBootstrapState(
            phase=self._phase,
            interaction_id=self._interaction_id,
        )

    def _require_initialize_transition(
        self,
        transition: _InitializeTransition,
    ) -> None:
        if type(transition) is not _InitializeTransition:
            raise TypeError("Invalid initialize transition token.")
        if transition.generation is None:
            if transition.origin.phase is not BootstrapPhase.READY:
                raise RuntimeError("Invalid ready initialize transition.")
            return
        if (
            self._phase is not BootstrapPhase.INITIALIZING
            or self._active_initialize_generation != transition.generation
        ):
            raise RuntimeError("Initialize transition is no longer active.")

    def _restore_initialize(self, transition: _InitializeTransition) -> None:
        if transition.generation is None:
            self._phase = BootstrapPhase.READY
            self._interaction_id = None
            return
        self._active_initialize_generation = None
        self._phase = transition.origin.phase
        self._interaction_id = transition.origin.interaction_id


def _state_from_initialize_result(
    value: InitializeResult | None,
) -> _StableBootstrapState:
    if type(value) is not InitializeResult:
        raise TypeError("Initialize returned an invalid result contract.")
    if type(value.status) is not InitializeStatus:
        raise ValueError("Initialize returned an unknown bootstrap status.")
    if value.status is InitializeStatus.READY:
        if value.interaction_id is not None:
            raise ValueError("Ready initialize result forbids an interaction ID.")
        return _StableBootstrapState(BootstrapPhase.READY, None)
    if value.status not in {
        InitializeStatus.TRUST_REQUIRED,
        InitializeStatus.STATE_RESET_REQUIRED,
    }:
        raise ValueError("Initialize returned an unknown bootstrap status.")
    if (
        type(value.interaction_id) is not str
        or not value.interaction_id.strip()
        or len(value.interaction_id) > 128
    ):
        raise ValueError("Bootstrap interaction requires an interaction ID.")
    if value.status is InitializeStatus.TRUST_REQUIRED:
        phase = BootstrapPhase.TRUST_REQUIRED
    elif value.status is InitializeStatus.STATE_RESET_REQUIRED:
        phase = BootstrapPhase.STATE_RESET_REQUIRED
    else:
        raise AssertionError("Initialize status exhaustiveness is broken.")
    return _StableBootstrapState(phase, value.interaction_id)


def _state_from_initialize_outcome(
    result: ApplicationResult[InitializeResult],
) -> _StableBootstrapState | None:
    if not isinstance(result, ApplicationResult) or type(result.ok) is not bool:
        raise TypeError("Initialize returned an invalid application result.")
    if result.ok is False:
        if result.value is not None or result.error is None:
            raise ValueError("Initialize failure has an invalid result branch.")
        return None
    if result.error is not None:
        raise ValueError("Successful initialize result forbids an error.")
    return _state_from_initialize_result(result.value)


__all__ = [
    "ApplicationBootstrap",
    "BootstrapPhase",
    "BootstrapRejection",
]
