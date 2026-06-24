from awesome_agent.domain.enums import ExecutionOrigin
from awesome_agent.sandbox.base import SandboxBackend
from awesome_agent.sandbox.docker import DockerSandbox
from awesome_agent.sandbox.local import TrustedLocalSandbox


def create_sandbox(
    *,
    origin: ExecutionOrigin,
    trusted_local: bool,
) -> SandboxBackend:
    if trusted_local:
        if origin is ExecutionOrigin.API:
            raise ValueError("FastAPI runs cannot use trusted-local execution.")
        return TrustedLocalSandbox()
    return DockerSandbox()
