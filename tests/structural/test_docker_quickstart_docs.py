from pathlib import Path


def test_docker_quickstart_services_are_declared() -> None:
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")

    assert "  postgres:" in compose
    assert "  api:" in compose
    assert "  worker:" in compose
    assert "AWESOME_AGENT_ARTIFACT_ROOT" in compose
    assert "/var/lib/awesome-agent/runs" in compose
    assert "AWESOME_AGENT_WORKSPACE_ROOT" in compose
    assert "/var/lib/awesome-agent/worktrees" in compose
    assert '"--unsafe-bind-public"' in compose


def test_docker_quickstart_documented_commands_are_implemented() -> None:
    script = Path("scripts/docker-quickstart.ps1")
    quickstart = Path("docs/getting-started/quickstart.md").read_text(
        encoding="utf-8"
    )

    assert script.is_file()
    assert ".\\scripts\\docker-quickstart.ps1" in quickstart
    assert "docker compose up -d --build postgres api worker" in quickstart
