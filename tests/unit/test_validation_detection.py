from __future__ import annotations

import json
from pathlib import Path

from awesome_agent.runtime.validation.detection import detect_validation_plan


def test_detects_python_validation_gates_from_pyproject(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        """
        [tool.ruff]
        line-length = 88

        [tool.mypy]
        strict = true

        [tool.pytest.ini_options]
        testpaths = ["tests"]
        """,
        encoding="utf-8",
    )

    plan = detect_validation_plan(tmp_path)

    assert plan is not None
    assert plan.source == "detected"
    assert [(gate.id, gate.command) for gate in plan.gates] == [
        ("ruff", ["ruff", "check", "."]),
        ("mypy", ["mypy", "."]),
        ("pytest", ["pytest", "-q"]),
    ]


def test_detects_safe_node_validation_scripts(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "scripts": {
                    "lint": "eslint .",
                    "test": "vitest run",
                }
            }
        ),
        encoding="utf-8",
    )

    plan = detect_validation_plan(tmp_path)

    assert plan is not None
    assert [(gate.id, gate.command) for gate in plan.gates] == [
        ("npm-lint", ["npm", "run", "lint"]),
        ("npm-test", ["npm", "run", "test"]),
    ]


def test_ignores_unsafe_node_scripts(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "scripts": {
                    "lint": "eslint . && npm install",
                    "test": "docker compose up",
                    "postinstall": "node scripts/setup.js",
                }
            }
        ),
        encoding="utf-8",
    )

    assert detect_validation_plan(tmp_path) is None


def test_returns_none_without_strong_validation_signals(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        """
        [project]
        name = "fixture"
        """,
        encoding="utf-8",
    )

    assert detect_validation_plan(tmp_path) is None
