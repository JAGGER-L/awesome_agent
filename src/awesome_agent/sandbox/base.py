from __future__ import annotations

from pathlib import Path
from typing import Protocol

from pydantic import BaseModel


class CommandResult(BaseModel):
    command: str
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False


class SandboxBackend(Protocol):
    async def execute(
        self,
        command: str,
        *,
        workspace: Path,
        timeout_seconds: float,
    ) -> CommandResult:
        """Execute a command inside the backend."""
        ...
