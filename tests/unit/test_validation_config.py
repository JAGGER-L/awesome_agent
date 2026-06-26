from __future__ import annotations

from pathlib import Path

import pytest

from awesome_agent.runtime.validation.config import (
    ValidationConfigError,
    load_validation_config,
)


def test_loads_validation_config_with_ordered_gates(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        """
        version = 1
        max_rework_attempts = 2

        [policy]
        fail_on_missing_required_gate = true

        [[gates]]
        id = "ruff"
        name = "Ruff lint"
        command = ["ruff", "check", "."]
        required = true
        timeout_seconds = 120

        [[gates]]
        id = "pytest"
        name = "Unit tests"
        command = ["pytest", "-q"]
        required = false
        timeout_seconds = 300
        """,
    )

    plan = load_validation_config(tmp_path)

    assert plan is not None
    assert plan.source == "configured"
    assert plan.max_rework_attempts == 2
    assert plan.fail_on_missing_required_gate
    assert [gate.id for gate in plan.gates] == ["ruff", "pytest"]
    assert plan.gates[0].command == ["ruff", "check", "."]
    assert plan.gates[0].required
    assert not plan.gates[1].required


def test_missing_validation_config_returns_none(tmp_path: Path) -> None:
    assert load_validation_config(tmp_path) is None


def test_rejects_duplicate_gate_ids(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        """
        version = 1

        [[gates]]
        id = "pytest"
        command = ["pytest", "-q"]

        [[gates]]
        id = "pytest"
        command = ["pytest", "tests/unit"]
        """,
    )

    with pytest.raises(ValidationConfigError, match="Duplicate validation gate id"):
        load_validation_config(tmp_path)


def test_rejects_shell_string_commands(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        """
        version = 1

        [[gates]]
        id = "pytest"
        command = "pytest -q"
        """,
    )

    with pytest.raises(ValidationConfigError, match="command must be an argv list"):
        load_validation_config(tmp_path)


def test_rejects_invalid_gate_id_and_timeout(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        """
        version = 1

        [[gates]]
        id = "PyTest"
        command = ["pytest", "-q"]
        timeout_seconds = 0
        """,
    )

    with pytest.raises(ValidationConfigError, match="Gate id"):
        load_validation_config(tmp_path)


def _write_config(tmp_path: Path, content: str) -> None:
    directory = tmp_path / ".agents"
    directory.mkdir()
    (directory / "validation.toml").write_text(content, encoding="utf-8")
