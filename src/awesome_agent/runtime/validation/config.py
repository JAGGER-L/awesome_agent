from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

from awesome_agent.runtime.validation.models import ValidationGate, ValidationPlan

_CONFIG_PATH = Path(".agents") / "validation.toml"
_GATE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*$")
_MAX_TIMEOUT_SECONDS = 3600
_MAX_REWORK_ATTEMPTS = 5


class ValidationConfigError(ValueError):
    pass


def load_validation_config(workspace: Path) -> ValidationPlan | None:
    path = workspace / _CONFIG_PATH
    if not path.exists():
        return None
    if not path.is_file():
        raise ValidationConfigError(".agents/validation.toml is not a file.")
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8-sig"))
    except tomllib.TOMLDecodeError as error:
        raise ValidationConfigError(f"Invalid validation.toml: {error}") from error
    return _parse_config(raw)


def _parse_config(raw: dict[str, Any]) -> ValidationPlan:
    version = raw.get("version")
    if version != 1:
        raise ValidationConfigError("validation.toml version must be 1.")

    max_rework_attempts = _int_field(
        raw,
        "max_rework_attempts",
        default=2,
        minimum=0,
        maximum=_MAX_REWORK_ATTEMPTS,
    )
    policy = raw.get("policy", {})
    if policy is None:
        policy = {}
    if not isinstance(policy, dict):
        raise ValidationConfigError("policy must be a table.")
    fail_on_missing_required_gate = _bool_field(
        policy,
        "fail_on_missing_required_gate",
        default=True,
    )

    gates_raw = raw.get("gates")
    if not isinstance(gates_raw, list) or not gates_raw:
        raise ValidationConfigError("validation.toml must define at least one gate.")

    seen: set[str] = set()
    gates: list[ValidationGate] = []
    for index, item in enumerate(gates_raw, start=1):
        if not isinstance(item, dict):
            raise ValidationConfigError(f"Gate {index} must be a table.")
        gate = _parse_gate(item, index=index)
        if gate.id in seen:
            raise ValidationConfigError(f"Duplicate validation gate id: {gate.id}")
        seen.add(gate.id)
        gates.append(gate)

    return ValidationPlan(
        gates=gates,
        source="configured",
        max_rework_attempts=max_rework_attempts,
        fail_on_missing_required_gate=fail_on_missing_required_gate,
    )


def _parse_gate(raw: dict[str, Any], *, index: int) -> ValidationGate:
    gate_id = raw.get("id")
    if not isinstance(gate_id, str) or not _GATE_ID_PATTERN.fullmatch(gate_id):
        raise ValidationConfigError(
            f"Gate id at index {index} must be a lowercase slug."
        )

    name = raw.get("name", gate_id)
    if not isinstance(name, str) or not name.strip():
        raise ValidationConfigError(f"Gate {gate_id} name must be a non-empty string.")

    command = raw.get("command")
    if not isinstance(command, list) or not command:
        raise ValidationConfigError(f"Gate {gate_id} command must be an argv list.")
    if not all(isinstance(part, str) and part for part in command):
        raise ValidationConfigError(
            f"Gate {gate_id} command must contain non-empty string arguments."
        )

    return ValidationGate(
        id=gate_id,
        name=name.strip(),
        command=list(command),
        required=_bool_field(raw, "required", default=True),
        timeout_seconds=_int_field(
            raw,
            "timeout_seconds",
            default=300,
            minimum=1,
            maximum=_MAX_TIMEOUT_SECONDS,
        ),
    )


def _bool_field(raw: dict[str, Any], name: str, *, default: bool) -> bool:
    value = raw.get(name, default)
    if not isinstance(value, bool):
        raise ValidationConfigError(f"{name} must be a boolean.")
    return value


def _int_field(
    raw: dict[str, Any],
    name: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    value = raw.get(name, default)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValidationConfigError(f"{name} must be an integer.")
    if value < minimum or value > maximum:
        raise ValidationConfigError(f"{name} must be between {minimum} and {maximum}.")
    return value
