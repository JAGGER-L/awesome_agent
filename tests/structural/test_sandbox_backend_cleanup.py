from pathlib import Path


def test_no_one_shot_docker_backend_remains() -> None:
    source_paths = list(Path("src/awesome_agent").rglob("*.py"))
    combined = "\n".join(path.read_text(encoding="utf-8") for path in source_paths)

    assert "docker-run" not in combined
    assert "DockerRunSandbox" not in combined
    assert "OneShotDocker" not in combined
    assert "python:3.12-slim" not in combined


def test_only_supported_sandbox_backends_are_documented() -> None:
    settings = Path("src/awesome_agent/settings.py").read_text(encoding="utf-8")

    assert '"aio-docker"' in settings
    assert '"local"' in settings
    assert '"docker-run"' not in settings
