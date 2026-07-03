import pytest
from pydantic import ValidationError
from tests.type_helpers import test_settings


def test_default_sandbox_backend_is_aio_docker_for_api_profiles() -> None:
    settings = test_settings()

    assert settings.sandbox_backend == "aio-docker"
    assert settings.local_cli_sandbox_backend == "local"
    assert settings.aio_sandbox_url == "http://127.0.0.1:8765"
    assert settings.readiness_check_docker


def test_unknown_sandbox_backend_is_rejected() -> None:
    with pytest.raises(ValidationError):
        test_settings(sandbox_backend="ssh")
