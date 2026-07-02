from awesome_agent.domain.enums import ExecutionOrigin
from awesome_agent.sandbox.aio import AioDockerSandbox
from awesome_agent.sandbox.base import SandboxBackend
from awesome_agent.sandbox.local import LocalSandbox
from awesome_agent.settings import Settings


def create_sandbox(
    *,
    origin: ExecutionOrigin,
    settings: Settings | None = None,
    profile: str | None = None,
) -> SandboxBackend:
    resolved = settings or Settings()
    backend = (
        resolved.local_cli_sandbox_backend
        if profile == "local-cli"
        else resolved.sandbox_backend
    )
    if origin is ExecutionOrigin.API and backend == "local":
        raise ValueError("API runs cannot use LocalSandbox execution.")
    if backend == "local":
        return LocalSandbox()
    return AioDockerSandbox(base_url=resolved.aio_sandbox_url)
