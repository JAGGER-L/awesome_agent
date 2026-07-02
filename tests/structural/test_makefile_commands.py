from pathlib import Path

REQUIRED_TARGETS = [
    "check",
    "install",
    "setup-sandbox",
    "dev",
    "docker-init",
    "docker-start",
    "docker-stop",
]


def test_makefile_declares_primary_startup_targets() -> None:
    text = Path("Makefile").read_text(encoding="utf-8")

    for target in REQUIRED_TARGETS:
        assert f"{target}:" in text


def test_makefile_delegates_to_scripts() -> None:
    text = Path("Makefile").read_text(encoding="utf-8")

    for script in [
        "scripts/make/check.py",
        "scripts/make/install.py",
        "scripts/make/setup_sandbox.py",
        "scripts/make/dev.py",
        "scripts/make/docker_init.py",
        "scripts/make/docker_start.py",
    ]:
        assert script in text


def test_docker_start_docs_do_not_start_cli() -> None:
    quickstart = Path("docs/getting-started/quickstart.md").read_text(
        encoding="utf-8"
    )

    assert "make docker-init" in quickstart
    assert "make docker-start" in quickstart
    assert "Docker mode does not start the CLI" in quickstart
