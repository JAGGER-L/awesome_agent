from pathlib import Path

from awesome_agent.paths import AwesomePaths


def test_windows_default_home_uses_localappdata(tmp_path: Path) -> None:
    localappdata = tmp_path / "LocalAppData"

    paths = AwesomePaths.resolve(
        environ={"LOCALAPPDATA": str(localappdata)},
        home=tmp_path / "home",
        platform="win32",
    )

    assert paths.home == localappdata / "awesome-agent"
    assert paths.install_dir == paths.home / "app"
    assert paths.env_file == paths.home / ".env"
    assert paths.config_file == paths.home / "config.yaml"
    assert paths.user_extension_config == paths.home / "awesome-agent.yaml"
    assert paths.skills_dir == paths.home / "skills"
    assert paths.state_dir == paths.home / "state"
    assert paths.runs_dir == paths.home / "runs"
    assert paths.logs_dir == paths.home / "logs"


def test_non_windows_default_home_uses_dot_directory(tmp_path: Path) -> None:
    paths = AwesomePaths.resolve(environ={}, home=tmp_path / "home", platform="linux")

    assert paths.home == tmp_path / "home" / ".awesome-agent"
    assert paths.install_dir == paths.home / "app"


def test_env_overrides_home_and_install_dir(tmp_path: Path) -> None:
    paths = AwesomePaths.resolve(
        environ={
            "AWESOME_HOME": str(tmp_path / "data"),
            "AWESOME_INSTALL_DIR": str(tmp_path / "code"),
            "LOCALAPPDATA": str(tmp_path / "LocalAppData"),
        },
        home=tmp_path / "home",
        platform="win32",
    )

    assert paths.home == tmp_path / "data"
    assert paths.install_dir == tmp_path / "code"


def test_target_state_databases_have_separate_paths(tmp_path: Path) -> None:
    paths = AwesomePaths.from_home(tmp_path / "awesome-home")

    assert paths.application_db == paths.state_dir / "application.db"
    assert paths.checkpoint_db == paths.state_dir / "checkpoints.db"
    assert paths.application_db != paths.checkpoint_db
