from __future__ import annotations

import httpx

from awesome_agent.sandbox.base import CommandRequest, CommandResult


class AioDockerSandbox:
    name = "aio-docker"

    def __init__(
        self,
        *,
        base_url: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.transport = transport

    async def execute(self, request: CommandRequest) -> CommandResult:
        payload = {
            "argv": request.argv,
            "workspace": str(request.workspace),
            "timeout_seconds": request.timeout_seconds,
            "max_output_chars": request.max_output_chars,
            "environment": dict(request.environment),
        }
        timeout = httpx.Timeout(request.timeout_seconds + 5)
        async with httpx.AsyncClient(
            base_url=self.base_url,
            transport=self.transport,
            timeout=timeout,
        ) as client:
            try:
                response = await client.post("/execute", json=payload)
                response.raise_for_status()
            except httpx.HTTPError as error:
                return CommandResult(
                    command=request.command_label,
                    exit_code=-1,
                    stdout="",
                    stderr=str(error),
                    timed_out=False,
                    sandbox=self.name,
                )
        data = response.json()
        return CommandResult(
            command=str(data.get("command", request.command_label)),
            exit_code=int(data.get("exit_code", -1)),
            stdout=str(data.get("stdout", "")),
            stderr=str(data.get("stderr", "")),
            timed_out=bool(data.get("timed_out", False)),
            sandbox=self.name,
        )
