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
    quickstart = Path("docs/getting-started/quickstart.md").read_text(encoding="utf-8")

    assert "make docker-init" in quickstart
    assert "make docker-start" in quickstart
    assert "Docker API does not start Local CLI" in quickstart


def test_docker_scripts_include_sandbox_service() -> None:
    docker_init = Path("scripts/make/docker_init.py").read_text(encoding="utf-8")
    docker_start = Path("scripts/make/docker_start.py").read_text(encoding="utf-8")
    setup_sandbox = Path("scripts/make/setup_sandbox.py").read_text(encoding="utf-8")

    assert '"sandbox"' in docker_init
    assert '"sandbox"' in docker_start
    assert '"alembic", "upgrade", "head"' in docker_start
    assert "awesome-agent-sandbox:aio" in setup_sandbox


def test_startup_scripts_do_not_create_repository_provider_env() -> None:
    for script in [
        Path("scripts/make/dev.py"),
        Path("scripts/make/docker_start.py"),
    ]:
        text = script.read_text(encoding="utf-8")
        assert 'ROOT / ".env"' not in text
        assert 'copyfile(ROOT / ".env.example"' not in text
        assert "awesome_env" in text


def test_install_script_syncs_dev_env_and_installs_cli_tool() -> None:
    install = Path("scripts/make/install.py").read_text(encoding="utf-8")

    assert '"sync"' in install
    assert '"--dev"' in install
    assert '"tool"' in install
    assert '"install"' in install
    assert '"--editable"' in install
    assert '"--force"' in install
    assert '"update-shell"' in install
    assert "awesome --help" in install


def test_install_script_checks_uv_before_installing() -> None:
    install = Path("scripts/make/install.py").read_text(encoding="utf-8")

    assert 'shutil.which("uv")' in install
