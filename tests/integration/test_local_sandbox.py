import os
from pathlib import Path

import pytest

from awesome_agent.sandbox.base import CommandRequest
from awesome_agent.sandbox.local import LocalSandbox
from awesome_agent.sandbox.path_mapping import WorkspacePathMapper

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_local_sandbox_executes_only_when_selected(tmp_path: Path) -> None:
    sandbox = LocalSandbox()

    result = await sandbox.execute(
        CommandRequest(
            argv=["Write-Output", "local-ok"],
            workspace=tmp_path,
            timeout_seconds=10,
        )
    )

    assert result.exit_code == 0
    assert result.stdout.strip() == "local-ok"
    assert result.sandbox == "local"


@pytest.mark.asyncio
async def test_local_sandbox_enforces_timeout(tmp_path: Path) -> None:
    sandbox = LocalSandbox()

    result = await sandbox.execute(
        CommandRequest(
            argv=["Start-Sleep", "-Seconds", "2"],
            workspace=tmp_path,
            timeout_seconds=0.1,
        )
    )

    assert result.timed_out
    assert result.exit_code == -1


@pytest.mark.asyncio
async def test_local_sandbox_maps_logical_workspace(tmp_path: Path) -> None:
    host_workspace = tmp_path / "thread" / "workspace"
    sandbox = LocalSandbox(
        path_mapper=WorkspacePathMapper(thread_workspace=host_workspace)
    )
    argv = (
        ["Set-Content", "-Path", "mapped.txt", "-Value", "ok"]
        if os.name == "nt"
        else ["python", "-c", "open('mapped.txt','w').write('ok')"]
    )

    result = await sandbox.execute(
        CommandRequest(
            argv=argv,
            workspace=Path("/mnt/user-data/workspace"),
            timeout_seconds=10,
        )
    )

    assert result.exit_code == 0
    assert (host_workspace / "mapped.txt").read_text(encoding="utf-8").strip() == "ok"


@pytest.mark.asyncio
async def test_local_sandbox_scrubs_provider_secrets_from_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "host-secret")
    sandbox = LocalSandbox()
    argv = (
        ["Write-Output", "$env:OPENAI_API_KEY; Write-Output $env:SAFE_FLAG"]
        if os.name == "nt"
        else [
            "python",
            "-c",
            (
                "import os; "
                "print(os.environ.get('OPENAI_API_KEY', '<missing>')); "
                "print(os.environ.get('SAFE_FLAG', '<missing>'))"
            ),
        ]
    )

    result = await sandbox.execute(
        CommandRequest(
            argv=argv,
            workspace=tmp_path,
            timeout_seconds=5,
            max_output_chars=1000,
            environment={
                "OPENAI_API_KEY": "request-secret",
                "SAFE_FLAG": "visible",
            },
        )
    )

    assert result.exit_code == 0
    assert "request-secret" not in result.stdout
    assert "host-secret" not in result.stdout
    assert "visible" in result.stdout
