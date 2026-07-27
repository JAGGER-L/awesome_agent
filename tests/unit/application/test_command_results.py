from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from awesome_agent.application.command_results import (
    COMMAND_OUTCOME_ADAPTER,
    CommandOption,
    CommandSecretPrompt,
    CommandSelection,
    NoticeCommandPayload,
    ThreadExportCommandPayload,
    ThreadRetryCommandPayload,
    ThreadTransitionCommandPayload,
    ThreadTransitionSnapshot,
    error,
    interaction,
    result,
)
from awesome_agent.application.contracts import (
    ApplicationState,
    OperationAccepted,
    ThreadReadResult,
    WorkspacePresentation,
)
from awesome_agent.config.models import BudgetConfig, SecretStatus
from awesome_agent.conversation import (
    Thread,
    ThreadEntry,
    ThreadEntryKind,
    ThreadLineage,
    ThreadView,
    Turn,
    TurnStatus,
)
from awesome_agent.core.tools.permissions import PermissionMode
from awesome_agent.modeling import MODEL_CATALOG


def test_untyped_arbitrary_command_result_is_rejected() -> None:
    with pytest.raises(ValidationError):
        COMMAND_OUTCOME_ADAPTER.validate_python(
            {"status": "success", "content": "", "data": {}}
        )


def test_result_and_error_use_exact_discriminators() -> None:
    success = result(NoticeCommandPayload(message="Ready"))
    failure = error("invalid_arguments", "Usage: /status")

    assert success.model_dump(mode="json") == {
        "kind": "result",
        "payload": {"kind": "notice", "message": "Ready"},
    }
    assert failure.model_dump(mode="json") == {
        "kind": "error",
        "code": "invalid_arguments",
        "message": "Usage: /status",
    }
    assert COMMAND_OUTCOME_ADAPTER.validate_python(success.model_dump()) == success
    assert COMMAND_OUTCOME_ADAPTER.validate_python(failure.model_dump()) == failure


def test_secret_interaction_serializes_metadata_only() -> None:
    outcome = interaction(
        CommandSecretPrompt(
            provider="deepseek",
            action="add",
            label="DeepSeek API Key",
            environment_variable="DEEPSEEK_API_KEY",
            help_url="https://platform.deepseek.com/api_keys",
        )
    )

    serialized = outcome.model_dump(mode="json", exclude_none=True)

    secret = serialized["interaction"]
    assert set(secret) == {
        "kind",
        "provider",
        "action",
        "label",
        "environment_variable",
        "help_url",
    }
    assert secret["kind"] == "secret"
    assert COMMAND_OUTCOME_ADAPTER.validate_python(serialized) == outcome


@pytest.mark.parametrize(
    "options",
    [
        (),
        (
            CommandOption(value="same", label="First"),
            CommandOption(value="same", label="Second"),
        ),
        (
            CommandOption(value="on", label="On", selected=True),
            CommandOption(value="off", label="Off", selected=True),
        ),
    ],
)
def test_selection_requires_options_unique_values_and_one_selection(
    options: tuple[CommandOption, ...],
) -> None:
    with pytest.raises(ValidationError):
        CommandSelection(prompt="Choose", options=options)


def test_unknown_and_secret_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        COMMAND_OUTCOME_ADAPTER.validate_python(
            {
                "kind": "interaction",
                "interaction": {
                    "kind": "secret",
                    "provider": "deepseek",
                    "action": "add",
                    "label": "DeepSeek API Key",
                    "environment_variable": "DEEPSEEK_API_KEY",
                    "help_url": "https://platform.deepseek.com/api_keys",
                    "api_key": "must-not-cross-the-contract",
                },
            }
        )


def test_thread_transition_requires_matching_application_and_thread_identity() -> None:
    thread = Thread(
        id="thread_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        workspace_key="workspace_1",
        title="Fixture Thread",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    application = ApplicationState.model_construct(
        initialized=True,
        session_id="session_fixture",
        workspace_key="workspace_1",
        workspace={"display_path": "E:/fixture"},
        workspace_trusted=True,
        current_thread_id="thread_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        thinking_enabled=False,
        skill_mode="auto",
        permission_mode=PermissionMode.REQUEST_APPROVAL,
        configuration_valid=True,
        secret_status=SecretStatus(),
    )

    with pytest.raises(ValidationError, match="identities must match"):
        ThreadTransitionCommandPayload(
            transition=ThreadTransitionSnapshot(
                reason="resume",
                application=application,
                thread=ThreadReadResult(view=ThreadView(thread=thread)),
            )
        )


def test_thread_transition_reason_requires_matching_lineage() -> None:
    now = datetime.now(UTC)
    root = Thread(
        id="thread_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        workspace_key="workspace_1",
        title="Root Thread",
        created_at=now,
        updated_at=now,
    )
    fork = root.model_copy(
        update={
            "lineage": ThreadLineage(
                kind="fork",
                source_thread_id="thread_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                source_turn_id="turn_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            )
        }
    )
    retry = root.model_copy(
        update={
            "lineage": ThreadLineage(
                kind="retry",
                source_thread_id="thread_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                source_turn_id="turn_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            )
        }
    )
    application = ApplicationState(
        initialized=True,
        session_id="session_fixture",
        workspace_key="workspace_1",
        workspace=WorkspacePresentation(display_path="E:/fixture"),
        workspace_trusted=True,
        current_thread_id=root.id,
        model_catalog=MODEL_CATALOG,
        thinking_enabled=False,
        skill_mode="auto",
        permission_mode=PermissionMode.REQUEST_APPROVAL,
        configuration_valid=True,
        secret_status=SecretStatus(),
    )

    def transition(reason: str, thread: Thread) -> ThreadTransitionSnapshot:
        return ThreadTransitionSnapshot.model_validate(
            {
                "reason": reason,
                "application": application,
                "thread": ThreadReadResult(view=ThreadView(thread=thread)),
            }
        )

    ThreadTransitionCommandPayload(transition=transition("new", root))
    ThreadTransitionCommandPayload(transition=transition("fork", fork))
    ThreadTransitionCommandPayload(transition=transition("resume", root))
    ThreadTransitionCommandPayload(transition=transition("resume", retry))

    with pytest.raises(ValidationError, match="root Thread"):
        ThreadTransitionCommandPayload(transition=transition("new", fork))
    with pytest.raises(ValidationError, match="Fork Thread lineage"):
        ThreadTransitionCommandPayload(transition=transition("fork", root))
    with pytest.raises(ValidationError, match="Fork Thread lineage"):
        ThreadTransitionCommandPayload(transition=transition("fork", retry))


def test_thread_retry_payload_requires_one_matching_transition_operation_turn() -> None:
    now = datetime.now(UTC)
    thread = Thread(
        id="thread_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        workspace_key="workspace_1",
        title="Retry Thread",
        lineage=ThreadLineage(
            kind="retry",
            source_thread_id="thread_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            source_turn_id="turn_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        ),
        created_at=now,
        updated_at=now,
    )
    entry = ThreadEntry(
        id="entry_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        thread_id=thread.id,
        sequence=1,
        kind=ThreadEntryKind.USER_MESSAGE,
        content="Retry this request.",
        client_message_id="client_retry",
        created_at=now,
    )
    turn = Turn(
        id="turn_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        thread_id=thread.id,
        checkpoint_key="turn_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        status=TurnStatus.IN_PROGRESS,
        provider="deepseek",
        model="deepseek-chat",
        budgets=BudgetConfig(),
        user_entry_id="entry_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        created_at=now,
        updated_at=now,
    )
    application = ApplicationState(
        initialized=True,
        session_id="session_fixture",
        workspace_key="workspace_1",
        workspace=WorkspacePresentation(display_path="E:/fixture"),
        workspace_trusted=True,
        current_thread_id=thread.id,
        model_catalog=MODEL_CATALOG,
        thinking_enabled=False,
        skill_mode="auto",
        permission_mode=PermissionMode.REQUEST_APPROVAL,
        configuration_valid=True,
        secret_status=SecretStatus(),
    )
    transition = ThreadTransitionSnapshot(
        reason="retry",
        application=application,
        thread=ThreadReadResult(
            view=ThreadView(thread=thread, entries=(entry,), turns=(turn,))
        ),
    )
    operation = OperationAccepted(
        operation_id="operation_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        thread_id=thread.id,
        turn_id=turn.id,
        client_message_id="client_retry",
    )

    payload = ThreadRetryCommandPayload(
        transition=transition,
        operation=operation,
    )

    assert payload.kind == "thread_retry"
    assert COMMAND_OUTCOME_ADAPTER.validate_python(
        result(payload).model_dump(mode="json")
    ) == result(payload)
    with pytest.raises(ValidationError, match="thread_retry payload"):
        ThreadTransitionCommandPayload(transition=transition)
    with pytest.raises(ValidationError, match="identities must match"):
        ThreadRetryCommandPayload(
            transition=transition,
            operation=operation.model_copy(update={"thread_id": "thread_other"}),
        )
    with pytest.raises(ValidationError, match="must belong"):
        ThreadRetryCommandPayload(
            transition=transition,
            operation=operation.model_copy(update={"turn_id": "turn_other"}),
        )
    with pytest.raises(ValidationError, match="client identity"):
        ThreadRetryCommandPayload(
            transition=transition,
            operation=operation.model_copy(
                update={"client_message_id": "client_other"}
            ),
        )
    missing_entry = transition.model_copy(
        update={
            "thread": ThreadReadResult(
                view=ThreadView(thread=thread, entries=(), turns=(turn,))
            )
        }
    )
    with pytest.raises(ValidationError, match="user Entry"):
        ThreadRetryCommandPayload(
            transition=missing_entry,
            operation=operation,
        )
    terminal = Turn.model_validate(
        turn.model_copy(
            update={
                "status": TurnStatus.CANCELLED,
                "termination_reason": "cancelled",
                "completed_at": now,
            }
        ).model_dump()
    )
    terminal_transition = transition.model_copy(
        update={
            "thread": ThreadReadResult(
                view=ThreadView(thread=thread, entries=(entry,), turns=(terminal,))
            )
        }
    )
    with pytest.raises(ValidationError, match="only in-progress"):
        ThreadRetryCommandPayload(
            transition=terminal_transition,
            operation=operation,
        )
    other_turn = turn.model_copy(
        update={
            "id": "turn_cccccccccccccccccccccccccccccccc",
            "checkpoint_key": "turn_cccccccccccccccccccccccccccccccc",
        }
    )
    multiple_in_progress = transition.model_copy(
        update={
            "thread": ThreadReadResult(
                view=ThreadView(
                    thread=thread,
                    entries=(entry,),
                    turns=(other_turn, turn),
                )
            )
        }
    )
    with pytest.raises(ValidationError, match="only in-progress"):
        ThreadRetryCommandPayload(
            transition=multiple_in_progress,
            operation=operation,
        )
    trailing_terminal = terminal.model_copy(
        update={
            "id": "turn_dddddddddddddddddddddddddddddddd",
            "checkpoint_key": "turn_dddddddddddddddddddddddddddddddd",
        }
    )
    operation_not_last = transition.model_copy(
        update={
            "thread": ThreadReadResult(
                view=ThreadView(
                    thread=thread,
                    entries=(entry,),
                    turns=(turn, trailing_terminal),
                )
            )
        }
    )
    with pytest.raises(ValidationError, match="final and only"):
        ThreadRetryCommandPayload(
            transition=operation_not_last,
            operation=operation,
        )


def test_thread_export_payload_serializes_the_stable_wire_contract() -> None:
    payload = ThreadExportCommandPayload(
        thread_id="fixture-thread",
        path="exports/thread.json",
        format="json",
        write_status="created",
        byte_count=321,
        change_set_id="changeset_1",
    )

    assert payload.model_dump(mode="json", exclude_none=True) == {
        "kind": "thread_export",
        "thread_id": "fixture-thread",
        "path": "exports/thread.json",
        "format": "json",
        "write_status": "created",
        "byte_count": 321,
        "change_set_id": "changeset_1",
    }
    assert COMMAND_OUTCOME_ADAPTER.validate_python(
        result(payload).model_dump(mode="json")
    ) == result(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {
            "thread_id": "fixture-thread",
            "path": "exports/thread.md",
            "format": "markdown",
            "write_status": "created",
            "byte_count": 1,
        },
        {
            "thread_id": "fixture-thread",
            "path": "exports/thread.md",
            "format": "markdown",
            "write_status": "unchanged",
            "byte_count": 1,
            "change_set_id": "changeset_1",
        },
    ],
)
def test_thread_export_payload_change_set_identity_matches_write_status(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError, match="exactly one ChangeSet"):
        ThreadExportCommandPayload.model_validate(payload)


def test_thread_export_payload_rejects_empty_change_set_identity() -> None:
    with pytest.raises(ValidationError):
        ThreadExportCommandPayload(
            thread_id="fixture-thread",
            path="exports/thread.md",
            format="markdown",
            write_status="created",
            byte_count=1,
            change_set_id="",
        )
