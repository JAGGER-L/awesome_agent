from pathlib import Path

from awesome_agent.sandbox.base import CommandResult
from awesome_agent.sandbox.process import run_process


class TrustedLocalSandbox:
    async def execute(
        self,
        command: str,
        *,
        workspace: Path,
        timeout_seconds: float,
    ) -> CommandResult:
        return await run_process(
            ["powershell", "-NoProfile", "-Command", command],
            command_label=command,
            workspace=workspace,
            timeout_seconds=timeout_seconds,
        )
