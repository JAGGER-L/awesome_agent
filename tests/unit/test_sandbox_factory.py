import pytest
from tests.type_helpers import test_settings

from awesome_agent.domain.enums import ExecutionOrigin
from awesome_agent.sandbox.aio import AioDockerSandbox
from awesome_agent.sandbox.factory import create_sandbox
from awesome_agent.sandbox.local import LocalSandbox


def test_api_uses_configured_aio_docker_by_default() -> None:
    sandbox = create_sandbox(
        origin=ExecutionOrigin.API,
        settings=test_settings(),
    )

    assert isinstance(sandbox, AioDockerSandbox)


def test_cli_profile_can_use_local_sandbox() -> None:
    sandbox = create_sandbox(
        origin=ExecutionOrigin.CLI,
        settings=test_settings(),
        profile="local-cli",
    )

    assert isinstance(sandbox, LocalSandbox)


def test_api_cannot_select_local_sandbox() -> None:
    settings = test_settings(sandbox_backend="local")

    with pytest.raises(ValueError, match="API runs cannot use LocalSandbox"):
        create_sandbox(origin=ExecutionOrigin.API, settings=settings)
