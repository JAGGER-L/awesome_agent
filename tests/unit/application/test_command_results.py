from __future__ import annotations

import pytest
from pydantic import ValidationError

from awesome_agent.application.command_results import (
    COMMAND_OUTCOME_ADAPTER,
    CommandOption,
    CommandSecretPrompt,
    CommandSelection,
    NoticeCommandPayload,
    error,
    interaction,
    result,
)


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
