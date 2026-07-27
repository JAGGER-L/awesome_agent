from pathlib import Path

from awesome_agent.paths import AwesomePaths


def test_windows_default_home_uses_localappdata(tmp_path: Path) -> None:
    localappdata = tmp_path / "LocalAppData"

    paths = AwesomePaths.resolve(
        environ={"LOCALAPPDATA": str(localappdata)},
        home=tmp_path / "home",
        platform="win32",
    )

    assert paths.home == localappdata / "Awesome"
    assert paths.install_dir == localappdata / "Programs" / "Awesome"
    assert paths.env_file == paths.home / ".env"
    assert paths.config_file == paths.home / "config.yaml"
    assert paths.skills_dir == paths.home / "skills"
    assert paths.logs_dir == paths.home / "logs"
    assert paths.state_dir == paths.home / "state"
    assert paths.change_journal_dir == paths.state_dir / "change-journal"
    assert paths.provider_model_transaction_file == (
        paths.state_dir / "provider-model-transaction.json"
    )
    assert paths.provider_credential_transaction_file == (
        paths.home / ".provider-credential-transaction.json"
    )
    assert paths.provider_credential_backup_file == (
        paths.home / ".provider-credential-transaction.env"
    )
    assert paths.ui_file == paths.home / "ui.json"


def test_non_windows_defaults_separate_data_and_install_roots(tmp_path: Path) -> None:
    home = tmp_path / "home"
    paths = AwesomePaths.resolve(environ={}, home=home, platform="linux")

    assert paths.home == home / ".awesome"
    assert paths.install_dir == home / ".local" / "share" / "awesome"


def test_macos_uses_the_posix_product_roots(tmp_path: Path) -> None:
    home = tmp_path / "home"
    paths = AwesomePaths.resolve(environ={}, home=home, platform="darwin")

    assert paths.home == home / ".awesome"
    assert paths.install_dir == home / ".local" / "share" / "awesome"


def test_env_overrides_home(tmp_path: Path) -> None:
    paths = AwesomePaths.resolve(
        environ={
            "AWESOME_HOME": str(tmp_path / "data"),
            "AWESOME_INSTALL_DIR": str(tmp_path / "program"),
            "LOCALAPPDATA": str(tmp_path / "LocalAppData"),
        },
        home=tmp_path / "home",
        platform="win32",
    )

    assert paths.home == tmp_path / "data"
    assert paths.install_dir == tmp_path / "program"


def test_state_databases_have_separate_paths(tmp_path: Path) -> None:
    paths = AwesomePaths.from_home(tmp_path / "awesome-home")

    assert paths.application_db == paths.state_dir / "application.db"
    assert paths.checkpoint_db == paths.state_dir / "checkpoints.db"
    assert paths.application_db != paths.checkpoint_db
    assert paths.change_journal_dir == paths.state_dir / "change-journal"
    assert paths.provider_model_transaction_file == (
        paths.state_dir / "provider-model-transaction.json"
    )
    assert paths.provider_credential_transaction_file.parent == paths.home
    assert paths.provider_credential_backup_file.parent == paths.home
    assert paths.provider_credential_transaction_file.parent != paths.state_dir
    assert paths.ui_file == paths.home / "ui.json"
    assert paths.user_memory_file == paths.memory_dir / "USER.md"
    assert paths.logs_dir == paths.home / "logs"
    assert paths.workspace_config_file(tmp_path / "workspace") == (
        tmp_path / "workspace" / ".awesome" / "config.yaml"
    )
