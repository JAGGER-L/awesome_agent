from __future__ import annotations

import os
from pathlib import Path

from awesome_agent.sandbox.base import CommandRequest, CommandResult
from awesome_agent.sandbox.path_mapping import WorkspacePathMapper
from awesome_agent.sandbox.process import run_process


class LocalSandbox:
    name = "local"

    def __init__(self, *, path_mapper: WorkspacePathMapper | None = None) -> None:
        self.path_mapper = path_mapper

    async def execute(self, request: CommandRequest) -> CommandResult:
        executable = "powershell" if os.name == "nt" else "bash"
        shell_args = (
            [executable, "-NoProfile", "-Command", request.command_label]
            if os.name == "nt"
            else [executable, "-lc", request.command_label]
        )
        workspace = self._workspace(request.workspace)
        workspace.mkdir(parents=True, exist_ok=True)
        result = await run_process(
            shell_args,
            command_label=request.command_label,
            workspace=workspace,
            timeout_seconds=request.timeout_seconds,
        )
        return result.model_copy(update={"sandbox": self.name})

    def _workspace(self, workspace: Path) -> Path:
        if self.path_mapper is None:
            return Path(workspace)
        return self.path_mapper.to_host_path(workspace)


TrustedLocalSandbox = LocalSandbox
