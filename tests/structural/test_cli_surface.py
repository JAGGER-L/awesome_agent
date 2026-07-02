from pathlib import Path


def test_awesome_script_is_declared() -> None:
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")

    assert 'awesome = "awesome_agent.cli.interactive:main"' in pyproject
    assert 'awesome-agent = "awesome_agent.cli.app:app"' in pyproject


def test_interactive_cli_documents_required_slash_commands() -> None:
    text = Path("docs/user-guide/README.md").read_text(encoding="utf-8")

    for command in ["/new", "/status", "/models", "/memory", "/help"]:
        assert command in text
