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
    (config_dir / "config.yaml").write_text("version: 1\n", encoding="utf-8")

    state = inspect_first_run_state(project_root=tmp_path, home=home)

    assert state.env_file_exists
    assert state.project_config_exists
    assert state.local_config_exists
    assert not state.needs_setup


def test_first_run_does_not_require_project_files_for_tui(tmp_path: Path) -> None:
    state = inspect_first_run_state(project_root=tmp_path / "project", home=tmp_path)

    assert state.local_config_exists is False
    assert state.needs_setup is True
    assert state.blocks_tui_launch is False


def test_first_run_needs_only_user_config_for_setup(tmp_path: Path) -> None:
    home = tmp_path / "home"
    config_dir = home / ".awesome-agent"
    config_dir.mkdir(parents=True)
    (config_dir / "config.yaml").write_text("version: 1\n", encoding="utf-8")

    state = inspect_first_run_state(project_root=tmp_path / "project", home=home)

    assert state.env_file_exists is False
    assert state.project_config_exists is False
    assert state.local_config_exists is True
    assert state.needs_setup is False
