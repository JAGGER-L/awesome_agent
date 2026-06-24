from pathlib import Path

from awesome_agent.sandbox.base import CommandResult
from awesome_agent.sandbox.process import run_process


class DockerSandbox:
    def __init__(
        self,
        *,
        image: str = "python:3.12-slim",
        network: str = "none",
        memory: str = "512m",
        cpus: str = "1.0",
    ) -> None:
        self._image = image
        self._network = network
        self._memory = memory
        self._cpus = cpus

    async def execute(
        self,
        command: str,
        *,
        workspace: Path,
        timeout_seconds: float,
    ) -> CommandResult:
        resolved = workspace.resolve()
        arguments = [
            "docker",
            "run",
            "--rm",
            "--network",
            self._network,
            "--memory",
            self._memory,
            "--cpus",
            self._cpus,
            "--volume",
            f"{resolved}:/workspace",
            "--workdir",
            "/workspace",
            self._image,
            "sh",
            "-lc",
            command,
        ]
        return await run_process(
            arguments,
            command_label=command,
            workspace=resolved,
            timeout_seconds=timeout_seconds,
        )
