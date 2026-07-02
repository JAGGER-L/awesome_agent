from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, Field


class CommandRequest(BaseModel):
    argv: list[str] = Field(min_length=1, max_length=128)
    workspace: Path
    timeout_seconds: float = Field(gt=0, le=3600)
    max_output_chars: int = Field(default=30_000, ge=1_000, le=200_000)
    environment: Mapping[str, str] = Field(default_factory=dict)

    @property
    def command_label(self) -> str:
        return " ".join(self.argv)


class CommandResult(BaseModel):
    command: str
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False
    sandbox: str = ""


class SandboxBackend(Protocol):
    name: str

    async def execute(self, request: CommandRequest) -> CommandResult:
        """Execute a command inside the backend."""
        ...
