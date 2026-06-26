from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

from awesome_agent.runtime.validation.models import ValidationGate, ValidationPlan

_SCRIPT_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_./:@+-]+$")
_SCRIPT_FORBIDDEN_TOKENS = {
    "&&",
    "||",
    ";",
    "|",
    ">",
    "<",
    "install",
    "add",
    "deploy",
    "publish",
    "migrate",
    "migration",
    "compose",
    "docker",
}
_SAFE_SCRIPT_BINARIES = {
    "eslint",
    "jest",
    "vitest",
    "tsx",
    "tsc",
}


def detect_validation_plan(workspace: Path) -> ValidationPlan | None:
    gates = [
        *_detect_pyproject_gates(workspace / "pyproject.toml"),
        *_detect_package_json_gates(workspace / "package.json"),
    ]
    if not gates:
        return None
    return ValidationPlan(
        gates=gates,
        source="detected",
        max_rework_attempts=2,
        fail_on_missing_required_gate=True,
    )


def _detect_pyproject_gates(path: Path) -> list[ValidationGate]:
    if not path.is_file():
        return []
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError:
        return []
    tool = data.get("tool")
    if not isinstance(tool, dict):
        return []
    gates: list[ValidationGate] = []
    if isinstance(tool.get("ruff"), dict):
        gates.append(
            ValidationGate(
                id="ruff",
                name="Ruff lint",
                command=["ruff", "check", "."],
                required=True,
                timeout_seconds=120,
            )
        )
    if isinstance(tool.get("mypy"), dict):
        gates.append(
            ValidationGate(
                id="mypy",
                name="Mypy type check",
                command=["mypy", "."],
                required=True,
                timeout_seconds=300,
            )
        )
    pytest_configured = False
    pytest_options = tool.get("pytest")
    if isinstance(pytest_options, dict):
        pytest_configured = isinstance(pytest_options.get("ini_options"), dict)
    if pytest_configured:
        gates.append(
            ValidationGate(
                id="pytest",
                name="Pytest",
                command=["pytest", "-q"],
                required=True,
                timeout_seconds=300,
            )
        )
    return gates


def _detect_package_json_gates(path: Path) -> list[ValidationGate]:
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if not isinstance(data, dict):
        return []
    scripts = data.get("scripts")
    if not isinstance(scripts, dict):
        return []
    gates: list[ValidationGate] = []
    for script_name, gate_id, gate_name, timeout in (
        ("lint", "npm-lint", "NPM lint", 120),
        ("test", "npm-test", "NPM test", 300),
    ):
        command = scripts.get(script_name)
        if isinstance(command, str) and _is_safe_node_script(command):
            gates.append(
                ValidationGate(
                    id=gate_id,
                    name=gate_name,
                    command=["npm", "run", script_name],
                    required=True,
                    timeout_seconds=timeout,
                )
            )
    return gates


def _is_safe_node_script(command: str) -> bool:
    tokens = command.split()
    if not tokens:
        return False
    if tokens[0] not in _SAFE_SCRIPT_BINARIES:
        return False
    return all(_is_safe_script_token(token) for token in tokens)


def _is_safe_script_token(token: str) -> bool:
    normalized = token.lower()
    if normalized in _SCRIPT_FORBIDDEN_TOKENS:
        return False
    if any(marker in normalized for marker in ("&&", "||", ";", "|", ">", "<")):
        return False
    return bool(_SCRIPT_TOKEN_PATTERN.fullmatch(token))
