from __future__ import annotations

from pydantic import BaseModel, Field


class ExecuteRequest(BaseModel):
    argv: list[str] = Field(min_length=1, max_length=128)
    workspace: str = "/mnt/user-data/workspace"
    timeout_seconds: float = Field(default=60, gt=0, le=3600)
    max_output_chars: int = Field(default=30_000, ge=1_000, le=200_000)
    environment: dict[str, str] = Field(default_factory=dict)


class ExecuteResponse(BaseModel):
    command: str
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False
    stdout_truncated: bool = False
    stderr_truncated: bool = False
