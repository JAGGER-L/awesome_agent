from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, Field


class ModelRequest(BaseModel):
    system_prompt: str
    user_prompt: str
    max_output_tokens: int = Field(default=2000, ge=1)


class ModelUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0


class ModelResult(BaseModel):
    text: str
    model: str
    provider: str
    response_id: str | None = None
    usage: ModelUsage = Field(default_factory=ModelUsage)


class ModelProvider(Protocol):
    async def generate(self, request: ModelRequest) -> ModelResult:
        """Generate one model response."""
        ...
