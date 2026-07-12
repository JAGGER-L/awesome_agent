from __future__ import annotations

from pathlib import Path

import pytest

from awesome_agent.config import (
    ConfigurationInvalid,
    config_source_paths,
    load_config_sources,
)
from awesome_agent.paths import AwesomePaths


def test_config_sources_are_exact_and_missing_files_are_not_created(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    paths = AwesomePaths.from_home(home)

    sources = config_source_paths(paths=paths, workspace=workspace)
    loaded = load_config_sources(
        paths=paths,
        workspace=workspace,
        workspace_trusted=True,
        environ={},
    )

    assert sources.user_config == home / "config.yaml"
    assert sources.user_env == home / ".env"
    assert sources.workspace_config == workspace / ".awesome" / "config.yaml"
    assert loaded.user.model_dump(mode="json") == {
        "version": 1,
        "providers": {"default_model": None, "kimi_region": "cn"},
        "credentials": {"deepseek": None, "kimi": None, "mem0": None},
        "budgets": {
            "model_calls": 32,
            "tool_calls": 64,
            "provider_retries": 2,
            "compressions": 2,
            "active_execution_seconds": 1800,
            "total_context_tokens": 262144,
        },
        "memory": {
            "local_file_memory": False,
            "mem0_cloud": False,
            "mem0_user_id": None,
        },
        "skills": {"disabled": []},
        "mcp_servers": [],
    }
    assert loaded.workspace is not None
    assert not home.exists()
    assert not workspace.exists()


@pytest.mark.parametrize(
    ("content", "expected_code"),
    [
        ("unknown: true\n", "configuration_invalid"),
        (
            "providers:\n  default_model: other/custom-model\n",
            "configuration_invalid",
        ),
        ("budgets:\n  model_calls: -1\n", "configuration_invalid"),
        (
            "budgets:\n  model_calls: 4\nbudgets:\n  tool_calls: 8\n",
            "duplicate_config_key",
        ),
    ],
)
def test_user_yaml_fails_closed(
    tmp_path: Path,
    content: str,
    expected_code: str,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.yaml").write_text(content, encoding="utf-8")

    with pytest.raises(ConfigurationInvalid) as raised:
        load_config_sources(
            paths=AwesomePaths.from_home(home),
            workspace=tmp_path / "workspace",
            workspace_trusted=False,
            environ={},
        )

    assert raised.value.code == expected_code
    assert str(home) not in str(raised.value)


@pytest.mark.parametrize(
    "content",
    [
        "MEM0_API_KEY: secret\n",
        "memory:\n  mem0_cloud: true\n",
        "providers:\n  default_model: deepseek/deepseek-v4-flash\n",
        "mcp_servers:\n  - id: server\n    command: python\n    enabled: true\n",
    ],
)
def test_workspace_yaml_cannot_claim_user_authority(
    tmp_path: Path,
    content: str,
) -> None:
    workspace = tmp_path / "workspace"
    config_dir = workspace / ".awesome"
    config_dir.mkdir(parents=True)
    (config_dir / "config.yaml").write_text(content, encoding="utf-8")

    with pytest.raises(ConfigurationInvalid):
        load_config_sources(
            paths=AwesomePaths.from_home(tmp_path / "home"),
            workspace=workspace,
            workspace_trusted=True,
            environ={},
        )


def test_untrusted_workspace_config_is_not_read(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    config_dir = workspace / ".awesome"
    config_dir.mkdir(parents=True)
    (config_dir / "config.yaml").write_text("unknown: true\n", encoding="utf-8")

    loaded = load_config_sources(
        paths=AwesomePaths.from_home(tmp_path / "home"),
        workspace=workspace,
        workspace_trusted=False,
        environ={},
    )

    assert loaded.workspace is None


def test_process_environment_overrides_user_dotenv_without_leaking_values(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / ".env").write_text(
        "DEEPSEEK_API_KEY=from-file\n"
        "MOONSHOT_API_KEY=moonshot-file\n"
        "MEM0_API_KEY=mem0-file\n",
        encoding="utf-8",
    )

    loaded = load_config_sources(
        paths=AwesomePaths.from_home(home),
        workspace=tmp_path / "workspace",
        workspace_trusted=False,
        environ={"DEEPSEEK_API_KEY": "from-process"},
    )

    assert loaded.secrets.deepseek_api_key is not None
    assert loaded.secrets.deepseek_api_key.get_secret_value() == "from-process"
    assert loaded.secrets.moonshot_api_key is not None
    assert loaded.secrets.mem0_api_key is not None
    assert loaded.secret_status.model_dump(mode="json") == {
        "deepseek_api_key": True,
        "moonshot_api_key": True,
        "mem0_api_key": True,
    }
    rendered = repr(loaded)
    assert "from-process" not in rendered
    assert "moonshot-file" not in rendered
    assert "mem0-file" not in rendered
    assert not hasattr(loaded.secrets, "model_dump")

    assert loaded.provider_credentials.model_dump(mode="json") == {
        "deepseek": {
            "provider": "deepseek",
            "environment_variable": "DEEPSEEK_API_KEY",
            "environment_configured": True,
            "awesome_configured": True,
            "selected_source": "environment",
        },
        "kimi": {
            "provider": "kimi",
            "environment_variable": "MOONSHOT_API_KEY",
            "environment_configured": False,
            "awesome_configured": True,
            "selected_source": "awesome",
        },
        "mem0": {
            "provider": "mem0",
            "environment_variable": "MEM0_API_KEY",
            "environment_configured": False,
            "awesome_configured": True,
            "selected_source": "awesome",
        },
    }


def test_missing_provider_credentials_report_missing_source(tmp_path: Path) -> None:
    loaded = load_config_sources(
        paths=AwesomePaths.from_home(tmp_path / "home"),
        workspace=tmp_path / "workspace",
        workspace_trusted=False,
        environ={},
    )

    assert loaded.provider_credentials.deepseek.environment_configured is False
    assert loaded.provider_credentials.deepseek.awesome_configured is False
    assert loaded.provider_credentials.deepseek.selected_source is None
    assert loaded.provider_credentials.kimi.environment_configured is False
    assert loaded.provider_credentials.kimi.awesome_configured is False
    assert loaded.provider_credentials.kimi.selected_source is None


def test_workspace_dotenv_is_never_loaded(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".env").write_text("MEM0_API_KEY=workspace-secret\n", encoding="utf-8")

    loaded = load_config_sources(
        paths=AwesomePaths.from_home(tmp_path / "home"),
        workspace=workspace,
        workspace_trusted=True,
        environ={},
    )

    assert loaded.secrets.mem0_api_key is None
