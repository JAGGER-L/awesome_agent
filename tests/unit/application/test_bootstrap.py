from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from awesome_agent.application.bootstrap import (
    ApplicationBootstrap,
    BootstrapPhase,
    BootstrapRejection,
)
from awesome_agent.application.contracts import (
    ApplicationResult,
    InitializeResult,
    InitializeStatus,
    InteractionResult,
    ProductError,
    ProductErrorCode,
    WorkspacePresentation,
)
from awesome_agent.application.middleware import ApplicationOperation


def _initialize_result(
    status: InitializeStatus,
    *,
    interaction_id: str | None = None,
) -> InitializeResult:
    return InitializeResult(
        product_version="0.1.0",
        protocol_version=3,
        status=status,
        session_id="session_1",
        interaction_id=interaction_id,
        workspace=WorkspacePresentation(display_path="C:\\workspace"),
    )


def _failure() -> ApplicationResult[InitializeResult]:
    return ApplicationResult.failure(
        ProductError(
            code=ProductErrorCode.STATE_UNAVAILABLE,
            message="State is temporarily unavailable.",
            retryable=True,
        )
    )


def _phase(bootstrap: ApplicationBootstrap) -> BootstrapPhase:
    return bootstrap.phase


def test_bootstrap_admission_is_one_closed_phase_table() -> None:
    bootstrap = ApplicationBootstrap()

    assert _phase(bootstrap) is BootstrapPhase.UNINITIALIZED
    assert bootstrap.rejection(ApplicationOperation.INITIALIZE) is None
    assert bootstrap.rejection(ApplicationOperation.CANCEL_OPERATION) is None
    assert bootstrap.rejection(ApplicationOperation.SHUTDOWN) is None
    assert bootstrap.rejection(ApplicationOperation.GET_STATE) == BootstrapRejection(
        message="Server not initialized",
        diagnostic_code="server_not_initialized",
    )
    assert bootstrap.rejection(None) == BootstrapRejection(
        message="Server not initialized",
        diagnostic_code="server_not_initialized",
    )

    transition = bootstrap.begin_initialize()

    assert _phase(bootstrap) is BootstrapPhase.INITIALIZING
    assert bootstrap.rejection(ApplicationOperation.INITIALIZE) == BootstrapRejection(
        message="Server initialization is in progress",
        diagnostic_code="initialization_in_progress",
    )
    assert bootstrap.rejection(ApplicationOperation.RESPOND_INTERACTION) == (
        BootstrapRejection(
            message="Server not ready",
            diagnostic_code="server_not_ready",
        )
    )
    bootstrap.complete_initialize(
        transition,
        ApplicationResult.success(
            _initialize_result(
                InitializeStatus.TRUST_REQUIRED,
                interaction_id="interaction_1",
            )
        ),
    )

    assert _phase(bootstrap) is BootstrapPhase.TRUST_REQUIRED
    assert bootstrap.interaction_id == "interaction_1"
    assert bootstrap.rejection(ApplicationOperation.INITIALIZE) is None
    assert bootstrap.rejection(ApplicationOperation.RESPOND_INTERACTION) is None
    assert bootstrap.rejection(ApplicationOperation.GET_STATE) == BootstrapRejection(
        message="Server not ready",
        diagnostic_code="server_not_ready",
    )


def test_bootstrap_rejection_is_frozen() -> None:
    rejection = ApplicationBootstrap().rejection(ApplicationOperation.GET_STATE)
    assert rejection is not None

    with pytest.raises(FrozenInstanceError):
        rejection.message = "changed"  # type: ignore[misc]


def test_initialize_failure_and_abort_restore_exact_interaction_phase() -> None:
    bootstrap = ApplicationBootstrap()
    initial = bootstrap.begin_initialize()
    bootstrap.complete_initialize(
        initial,
        ApplicationResult.success(
            _initialize_result(
                InitializeStatus.STATE_RESET_REQUIRED,
                interaction_id="interaction_reset",
            )
        ),
    )

    failed = bootstrap.begin_initialize()
    assert _phase(bootstrap) is BootstrapPhase.INITIALIZING
    assert bootstrap.interaction_id is None
    bootstrap.complete_initialize(failed, _failure())
    assert _phase(bootstrap) is BootstrapPhase.STATE_RESET_REQUIRED
    assert bootstrap.interaction_id == "interaction_reset"

    cancelled = bootstrap.begin_initialize()
    bootstrap.abort_initialize(cancelled)
    assert _phase(bootstrap) is BootstrapPhase.STATE_RESET_REQUIRED
    assert bootstrap.interaction_id == "interaction_reset"


def test_ready_repeat_never_closes_or_regresses_admission() -> None:
    bootstrap = ApplicationBootstrap()
    initial = bootstrap.begin_initialize()
    bootstrap.complete_initialize(
        initial,
        ApplicationResult.success(_initialize_result(InitializeStatus.READY)),
    )

    repeated = bootstrap.begin_initialize()
    assert _phase(bootstrap) is BootstrapPhase.READY
    assert bootstrap.rejection(ApplicationOperation.GET_STATE) is None
    bootstrap.complete_initialize(repeated, _failure())
    assert _phase(bootstrap) is BootstrapPhase.READY

    invalid_repeat = bootstrap.begin_initialize()
    with pytest.raises(RuntimeError, match="cannot regress"):
        bootstrap.complete_initialize(
            invalid_repeat,
            ApplicationResult.success(
                _initialize_result(
                    InitializeStatus.TRUST_REQUIRED,
                    interaction_id="interaction_new",
                )
            ),
        )
    assert _phase(bootstrap) is BootstrapPhase.READY


def test_initialize_interaction_status_requires_exact_typed_identity() -> None:
    bootstrap = ApplicationBootstrap()
    transition = bootstrap.begin_initialize()

    with pytest.raises(ValueError, match="requires an interaction ID"):
        bootstrap.complete_initialize(
            transition,
            ApplicationResult.success(
                _initialize_result(InitializeStatus.TRUST_REQUIRED)
            ),
        )

    assert _phase(bootstrap) is BootstrapPhase.UNINITIALIZED
    assert bootstrap.interaction_id is None


def test_constructed_unknown_initialize_status_restores_previous_phase() -> None:
    bootstrap = ApplicationBootstrap()
    transition = bootstrap.begin_initialize()
    malformed = InitializeResult.model_construct(
        product_version="0.1.0",
        protocol_version=3,
        status="future_status",
        session_id="session_1",
        interaction_id="interaction_future",
        workspace=WorkspacePresentation(display_path="C:\\workspace"),
        capabilities=(),
    )

    with pytest.raises(ValueError, match="unknown bootstrap status"):
        bootstrap.complete_initialize(
            transition,
            ApplicationResult.success(malformed),
        )

    assert _phase(bootstrap) is BootstrapPhase.UNINITIALIZED


def test_constructed_inconsistent_initialize_branch_fails_closed() -> None:
    bootstrap = ApplicationBootstrap()
    transition = bootstrap.begin_initialize()
    malformed = ApplicationResult[InitializeResult].model_construct(
        ok=True,
        value=_initialize_result(InitializeStatus.READY),
        error=ProductError(
            code=ProductErrorCode.INTERNAL_ERROR,
            message="Invalid branch.",
        ),
    )

    with pytest.raises(ValueError, match="forbids an error"):
        bootstrap.complete_initialize(transition, malformed)

    assert _phase(bootstrap) is BootstrapPhase.UNINITIALIZED


def test_only_exact_resolved_trust_opens_ready_and_reset_requires_reinitialize() -> (
    None
):
    bootstrap = ApplicationBootstrap()
    initial = bootstrap.begin_initialize()
    bootstrap.complete_initialize(
        initial,
        ApplicationResult.success(
            _initialize_result(
                InitializeStatus.TRUST_REQUIRED,
                interaction_id="interaction_trust",
            )
        ),
    )

    assert bootstrap.begin_interaction("interaction_stale", "trust") is None
    exact = bootstrap.begin_interaction("interaction_trust", "trust")
    assert exact is not None
    bootstrap.complete_interaction(
        exact,
        ApplicationResult.success(InteractionResult(accepted=True, status="denied")),
    )
    assert _phase(bootstrap) is BootstrapPhase.TRUST_REQUIRED

    exact = bootstrap.begin_interaction("interaction_trust", "trust")
    assert exact is not None
    bootstrap.complete_interaction(
        exact,
        ApplicationResult.success(InteractionResult(accepted=True, status="resolved")),
    )
    assert _phase(bootstrap) is BootstrapPhase.READY
    assert bootstrap.interaction_id is None

    reset_bootstrap = ApplicationBootstrap()
    reset = reset_bootstrap.begin_initialize()
    reset_bootstrap.complete_initialize(
        reset,
        ApplicationResult.success(
            _initialize_result(
                InitializeStatus.STATE_RESET_REQUIRED,
                interaction_id="interaction_reset",
            )
        ),
    )
    resolution = reset_bootstrap.begin_interaction(
        "interaction_reset",
        "reset_state",
    )
    assert resolution is not None
    reset_bootstrap.complete_interaction(
        resolution,
        ApplicationResult.success(InteractionResult(accepted=True, status="resolved")),
    )
    assert _phase(reset_bootstrap) is BootstrapPhase.STATE_RESET_REQUIRED
    assert reset_bootstrap.rejection(ApplicationOperation.GET_STATE) is not None

    reinitialize = reset_bootstrap.begin_initialize()
    reset_bootstrap.complete_initialize(
        reinitialize,
        ApplicationResult.success(_initialize_result(InitializeStatus.READY)),
    )
    assert _phase(reset_bootstrap) is BootstrapPhase.READY
    assert reset_bootstrap.rejection(ApplicationOperation.GET_STATE) is None
