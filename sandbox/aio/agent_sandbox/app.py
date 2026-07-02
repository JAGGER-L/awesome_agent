from __future__ import annotations

from fastapi import FastAPI

from sandbox.aio.agent_sandbox.executor import execute_command
from sandbox.aio.agent_sandbox.models import ExecuteRequest, ExecuteResponse

app = FastAPI(title="awesome-agent sandbox")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "healthy"}


@app.post("/execute")
async def execute(request: ExecuteRequest) -> ExecuteResponse:
    return await execute_command(request)
