from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ValidationPlanSource = Literal["configured", "detected"]


@dataclass(frozen=True, slots=True)
class ValidationGate:
    id: str
    name: str
    command: list[str]
    required: bool = True
    timeout_seconds: int = 300


@dataclass(frozen=True, slots=True)
class ValidationPlan:
    gates: list[ValidationGate]
    source: ValidationPlanSource
    max_rework_attempts: int = 2
    fail_on_missing_required_gate: bool = True
