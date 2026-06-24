import pytest

from awesome_agent.domain.enums import ExecutionOrigin
from awesome_agent.sandbox.docker import DockerSandbox
from awesome_agent.sandbox.factory import create_sandbox
from awesome_agent.sandbox.local import TrustedLocalSandbox


def test_cli_can_explicitly_select_trusted_local() -> None:
    sandbox = create_sandbox(origin=ExecutionOrigin.CLI, trusted_local=True)

    assert isinstance(sandbox, TrustedLocalSandbox)


def test_api_cannot_select_trusted_local() -> None:
    with pytest.raises(ValueError, match="FastAPI"):
        create_sandbox(origin=ExecutionOrigin.API, trusted_local=True)


def test_docker_is_default() -> None:
    sandbox = create_sandbox(origin=ExecutionOrigin.API, trusted_local=False)

    assert isinstance(sandbox, DockerSandbox)
