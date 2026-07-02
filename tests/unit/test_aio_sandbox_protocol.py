from sandbox.aio.agent_sandbox.models import ExecuteRequest, ExecuteResponse


def test_execute_request_defaults_to_thread_workspace() -> None:
    request = ExecuteRequest(argv=["python", "--version"])

    assert request.workspace == "/mnt/user-data/workspace"
    assert request.timeout_seconds == 60
    assert request.max_output_chars == 30000
    assert request.environment == {}


def test_execute_response_contract() -> None:
    response = ExecuteResponse(
        command="python --version",
        exit_code=0,
        stdout="Python 3.12",
        stderr="",
        timed_out=False,
        stdout_truncated=False,
        stderr_truncated=False,
    )

    assert response.exit_code == 0
    assert not response.timed_out
