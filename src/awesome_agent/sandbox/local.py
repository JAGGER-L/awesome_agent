from __future__ import annotations

import os
from pathlib import Path

from awesome_agent.sandbox.base import CommandRequest, CommandResult
from awesome_agent.sandbox.process import run_process


class LocalSandbox:
    name = "local"

    async def execute(self, request: CommandRequest) -> CommandResult:
        executable = "powershell" if os.name == "nt" else "bash"
        shell_args = (
            [executable, "-NoProfile", "-Command", request.command_label]
            if os.name == "nt"
            else [executable, "-lc", request.command_label]
        )
        result = await run_process(
            shell_args,
            command_label=request.command_label,
            workspace=Path(request.workspace),
            timeout_seconds=request.timeout_seconds,
        )
        return result.model_copy(update={"sandbox": self.name})


TrustedLocalSandbox = LocalSandbox
