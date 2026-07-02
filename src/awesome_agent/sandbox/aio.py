from __future__ import annotations

from awesome_agent.sandbox.base import CommandRequest, CommandResult


class AioDockerSandboxUnavailable(RuntimeError):
    pass


class AioDockerSandbox:
    name = "aio-docker"

    def __init__(self, *, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    async def execute(self, request: CommandRequest) -> CommandResult:
        raise AioDockerSandboxUnavailable(
            "AIO Docker sandbox HTTP execution is planned for Task 62."
        )
