from pathlib import Path

from awesome_agent.cli.first_run import inspect_first_run_state


def test_first_run_state_detects_missing_files(tmp_path: Path) -> None:
    state = inspect_first_run_state(project_root=tmp_path, home=tmp_path / "home")

    assert not state.env_file_exists
    assert not state.project_config_exists
    assert not state.local_config_exists
    assert state.needs_setup


def test_first_run_state_detects_ready_files(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("", encoding="utf-8")
    (tmp_path / "awesome-agent.yaml").write_text("version: 1\n", encoding="utf-8")
    home = tmp_path / "home"
    config_dir = home / ".awesome-agent"
    config_dir.mkdir(parents=True)
    (config_dir / "config.toml").write_text("", encoding="utf-8")

    state = inspect_first_run_state(project_root=tmp_path, home=home)

    assert state.env_file_exists
    assert state.project_config_exists
    assert state.local_config_exists
    assert not state.needs_setup
