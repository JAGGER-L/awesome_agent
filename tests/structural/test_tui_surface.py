from pathlib import Path


def test_tui_dependency_and_package_are_declared() -> None:
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")

    assert '"textual' in pyproject
    assert Path("src/awesome_agent/tui/__init__.py").is_file()
    assert Path("src/awesome_agent/tui/app.py").is_file()
    assert Path("src/awesome_agent/tui/client.py").is_file()
    assert Path("src/awesome_agent/tui/state.py").is_file()


def test_tui_is_documented_as_local_operator_console() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")
    user_guide = Path("docs/user-guide/README.md").read_text(encoding="utf-8")

    assert "awesome-agent.exe tui" in readme
    assert "TUI Operator Console" in user_guide
    assert "not a hosted web dashboard" in readme
