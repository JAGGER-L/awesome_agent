from __future__ import annotations

import asyncio
import os
import subprocess
import threading
import time
from pathlib import Path

import pytest

import awesome_agent.core.safe_files as safe_files_module
from awesome_agent.config import (
    ConfigurationInvalid,
    config_source_paths,
    load_config_sources,
)
from awesome_agent.config.loader import WORKSPACE_CONFIG_MAX_BYTES
from awesome_agent.config.resource_lock import exclusive_resource_lock
from awesome_agent.core.filesystem import DirectoryPin, ReadRegularFile
from awesome_agent.core.filesystem import FileIdentity as CoreFileIdentity
from awesome_agent.paths import AwesomePaths


def _directory_link(target: Path, link: Path) -> None:
    if os.name == "nt":
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            check=True,
            capture_output=True,
            text=True,
        )
        return
    os.symlink(target, link, target_is_directory=True)


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
        "version": 2,
        "providers": {"default_model": None, "kimi_region": "cn"},
        "credentials": {
            "deepseek": None,
            "kimi": None,
            "mem0": None,
            "tavily": "environment",
            "web_proxy": None,
        },
        "budgets": {
            "model_calls": 32,
            "tool_calls": 64,
            "provider_retries": 2,
            "compressions": 2,
            "active_execution_seconds": 1800,
            "total_context_tokens": 262144,
            "web_requests": 8,
        },
        "web": {
            "enabled": False,
            "provider": "tavily",
            "blocked_domains": [],
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
        "credentials:\n  tavily: environment\n",
        "web:\n  enabled: true\n",
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


@pytest.mark.parametrize(
    "content",
    [
        "version: true\n",
        "budgets:\n  model_calls: '4'\n",
    ],
)
@pytest.mark.parametrize("source", ["user", "workspace"])
def test_yaml_documents_reject_coerced_scalars(
    tmp_path: Path,
    content: str,
    source: str,
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    home.mkdir()
    workspace.mkdir()
    target = home / "config.yaml"
    if source == "workspace":
        target = workspace / ".awesome" / "config.yaml"
        target.parent.mkdir()
    target.write_text(content, encoding="utf-8")

    with pytest.raises(ConfigurationInvalid) as raised:
        load_config_sources(
            paths=AwesomePaths.from_home(home),
            workspace=workspace,
            workspace_trusted=True,
            environ={},
        )

    assert raised.value.code == "configuration_invalid"


@pytest.mark.parametrize(
    "content",
    [
        "memory:\n  local_file_memory: 'false'\n",
        "mcp_servers:\n  - id: server\n    command: python\n    enabled: 'true'\n",
        "providers:\n  kimi_region: !!binary Y24=\n",
    ],
)
def test_user_yaml_rejects_non_native_scalar_types(
    tmp_path: Path,
    content: str,
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    home.mkdir()
    workspace.mkdir()
    (home / "config.yaml").write_text(content, encoding="utf-8")

    with pytest.raises(ConfigurationInvalid) as raised:
        load_config_sources(
            paths=AwesomePaths.from_home(home),
            workspace=workspace,
            workspace_trusted=True,
            environ={},
        )

    assert raised.value.code == "configuration_invalid"


def test_native_yaml_scalars_enums_and_lists_remain_supported(tmp_path: Path) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    home.mkdir()
    (workspace / ".awesome").mkdir(parents=True)
    (home / "config.yaml").write_text(
        "version: 1\n"
        "providers:\n  kimi_region: global\n"
        "credentials:\n  kimi: environment\n"
        "budgets:\n  model_calls: 4\n"
        "memory:\n  local_file_memory: false\n"
        "skills:\n  disabled: [legacy-review]\n"
        "mcp_servers:\n"
        "  - id: user-server\n"
        "    command: python\n"
        "    args: [-m, server]\n"
        "    env: [SERVER_TOKEN]\n"
        "    enabled: false\n",
        encoding="utf-8",
    )
    (workspace / ".awesome" / "config.yaml").write_text(
        "version: 1\n"
        "budgets:\n  model_calls: 3\n"
        "skills:\n  disabled: [workspace-review]\n"
        "mcp_servers:\n"
        "  - id: workspace-server\n"
        "    command: python\n"
        "    args: [-m, workspace_server]\n"
        "    env: [WORKSPACE_SERVER_TOKEN]\n",
        encoding="utf-8",
    )

    loaded = load_config_sources(
        paths=AwesomePaths.from_home(home),
        workspace=workspace,
        workspace_trusted=True,
        environ={},
    )

    assert loaded.user.providers.kimi_region.value == "global"
    assert loaded.user.version == 2
    assert loaded.user.credentials.kimi is not None
    assert loaded.user.credentials.tavily.value == "environment"
    assert loaded.user.credentials.web_proxy is None
    assert loaded.user.budgets.model_calls == 4
    assert loaded.user.budgets.web_requests == 8
    assert loaded.user.web.enabled is False
    assert loaded.user.memory.local_file_memory is False
    assert loaded.user.skills.disabled == ("legacy-review",)
    assert loaded.user.mcp_servers[0].args == ("-m", "server")
    assert loaded.workspace is not None
    assert loaded.workspace.budgets.model_calls == 3
    assert loaded.workspace.mcp_servers[0].env == ("WORKSPACE_SERVER_TOKEN",)


def test_user_v1_is_upgraded_in_memory_without_rewriting_source(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    source = (
        "version: 1\n"
        "credentials:\n  deepseek: environment\n"
        "budgets:\n  model_calls: 12\n"
    )
    path = home / "config.yaml"
    path.write_text(source, encoding="utf-8")

    loaded = load_config_sources(
        paths=AwesomePaths.from_home(home),
        workspace=tmp_path / "workspace",
        workspace_trusted=False,
        environ={"DEEPSEEK_API_KEY": "configured"},
    )

    assert loaded.user.version == 2
    assert loaded.user.credentials.deepseek is not None
    assert loaded.user.credentials.tavily.value == "environment"
    assert loaded.user.credentials.web_proxy is None
    assert loaded.user.budgets.model_calls == 12
    assert loaded.user.budgets.web_requests == 8
    assert loaded.user.web.enabled is False
    assert path.read_text(encoding="utf-8") == source


@pytest.mark.parametrize(
    "content",
    [
        "version: 1\nweb:\n  enabled: true\n",
        "version: 1\ncredentials:\n  tavily: environment\n",
        "version: 1\nbudgets:\n  web_requests: 1\n",
    ],
)
def test_user_v1_rejects_v2_only_fields(tmp_path: Path, content: str) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.yaml").write_text(content, encoding="utf-8")

    with pytest.raises(ConfigurationInvalid):
        load_config_sources(
            paths=AwesomePaths.from_home(home),
            workspace=tmp_path / "workspace",
            workspace_trusted=False,
            environ={},
        )


@pytest.mark.parametrize(
    "content",
    [
        "version: 2\ncredentials:\n  tavily: awesome\n",
        "version: 2\nweb:\n  provider: other\n",
        "version: 2\nweb:\n  blocked_domains: [Example.COM]\n",
        "version: 2\nbudgets:\n  web_requests: 9\n",
    ],
)
def test_user_v2_web_contract_is_strict(tmp_path: Path, content: str) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.yaml").write_text(content, encoding="utf-8")

    with pytest.raises(ConfigurationInvalid):
        load_config_sources(
            paths=AwesomePaths.from_home(home),
            workspace=tmp_path / "workspace",
            workspace_trusted=False,
            environ={},
        )


def test_workspace_v1_can_only_lower_web_request_budget(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    config = workspace / ".awesome" / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text(
        "version: 1\nbudgets:\n  web_requests: 3\n",
        encoding="utf-8",
    )

    loaded = load_config_sources(
        paths=AwesomePaths.from_home(tmp_path / "home"),
        workspace=workspace,
        workspace_trusted=True,
        environ={},
    )

    assert loaded.workspace is not None
    assert loaded.workspace.version == 1
    assert loaded.workspace.budgets.web_requests == 3


def test_workspace_v1_can_add_web_domain_restrictions(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    config = workspace / ".awesome" / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text(
        "version: 1\nweb:\n  blocked_domains: [example.com]\n",
        encoding="utf-8",
    )

    loaded = load_config_sources(
        paths=AwesomePaths.from_home(tmp_path / "home"),
        workspace=workspace,
        workspace_trusted=True,
        environ={},
    )

    assert loaded.workspace is not None
    assert loaded.workspace.web.blocked_domains == ("example.com",)


@pytest.mark.parametrize("field", ["enabled: true", "provider: tavily"])
def test_workspace_v1_cannot_expand_web_authority(
    tmp_path: Path,
    field: str,
) -> None:
    workspace = tmp_path / "workspace"
    config = workspace / ".awesome" / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text(
        f"version: 1\nweb:\n  {field}\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationInvalid):
        load_config_sources(
            paths=AwesomePaths.from_home(tmp_path / "home"),
            workspace=workspace,
            workspace_trusted=True,
            environ={},
        )


@pytest.mark.parametrize(
    "data",
    [
        b"x" * (WORKSPACE_CONFIG_MAX_BYTES + 1),
        b"version: 1\x00hidden: true\n",
        b"version: 1\n# \xff\n",
    ],
    ids=["oversized", "nul", "invalid-utf8"],
)
def test_workspace_config_rejects_unbounded_or_non_text_input(
    tmp_path: Path,
    data: bytes,
) -> None:
    workspace = tmp_path / "workspace"
    config = workspace / ".awesome" / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_bytes(data)

    with pytest.raises(ConfigurationInvalid) as raised:
        load_config_sources(
            paths=AwesomePaths.from_home(tmp_path / "home"),
            workspace=workspace,
            workspace_trusted=True,
            environ={},
        )

    assert raised.value.code == "configuration_invalid"


def test_workspace_config_rejects_linked_parent_without_reading_target(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside-awesome"
    workspace.mkdir()
    outside.mkdir()
    sentinel = "EXTERNAL-WORKSPACE-CONFIG-SENTINEL"
    (outside / "config.yaml").write_text(
        f"version: 1\n# {sentinel}\n",
        encoding="utf-8",
    )
    _directory_link(outside, workspace / ".awesome")

    with pytest.raises(ConfigurationInvalid) as raised:
        load_config_sources(
            paths=AwesomePaths.from_home(tmp_path / "home"),
            workspace=workspace,
            workspace_trusted=True,
            environ={},
        )

    assert raised.value.code == "configuration_invalid"
    assert sentinel not in str(raised.value)


def test_workspace_config_rejects_hard_link_without_reading_target(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    config = workspace / ".awesome" / "config.yaml"
    config.parent.mkdir(parents=True)
    outside = tmp_path / "outside-config.yaml"
    sentinel = "EXTERNAL-HARD-LINK-CONFIG-SENTINEL"
    outside.write_text(f"version: 1\n# {sentinel}\n", encoding="utf-8")
    os.link(outside, config)

    with pytest.raises(ConfigurationInvalid) as raised:
        load_config_sources(
            paths=AwesomePaths.from_home(tmp_path / "home"),
            workspace=workspace,
            workspace_trusted=True,
            environ={},
        )

    assert raised.value.code == "configuration_invalid"
    assert sentinel not in str(raised.value)


def test_workspace_config_rejects_workspace_root_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    config = workspace / ".awesome" / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("version: 1\nbudgets:\n  model_calls: 4\n", encoding="utf-8")
    replacement = tmp_path / "replacement"
    replacement_config = replacement / ".awesome" / "config.yaml"
    replacement_config.parent.mkdir(parents=True)
    replacement_config.write_text(
        "version: 1\nbudgets:\n  model_calls: 1\n",
        encoding="utf-8",
    )
    original = tmp_path / "original"
    real_read = safe_files_module._read_pinned_regular_child
    replaced = False

    def replace_workspace_before_open(
        parent: DirectoryPin,
        name: str,
        *,
        max_bytes: int | None,
        expected_identity: CoreFileIdentity | None = None,
    ) -> ReadRegularFile:
        nonlocal replaced
        try:
            workspace.rename(original)
        except OSError:
            return real_read(
                parent,
                name,
                max_bytes=max_bytes,
                expected_identity=expected_identity,
            )
        replaced = True
        replacement.rename(workspace)
        try:
            return real_read(
                parent,
                name,
                max_bytes=max_bytes,
                expected_identity=expected_identity,
            )
        finally:
            workspace.rename(replacement)
            original.rename(workspace)

    monkeypatch.setattr(
        safe_files_module,
        "_read_pinned_regular_child",
        replace_workspace_before_open,
    )

    if replaced:
        raise AssertionError("replacement state leaked before config load")
    try:
        loaded = load_config_sources(
            paths=AwesomePaths.from_home(tmp_path / "home"),
            workspace=workspace,
            workspace_trusted=True,
            environ={},
        )
    except ConfigurationInvalid as raised:
        assert raised.code == "configuration_invalid"
    else:
        assert not replaced
        assert loaded.workspace is not None
        assert loaded.workspace.budgets.model_calls == 4


def test_process_environment_overrides_user_dotenv_without_leaking_values(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / ".env").write_text(
        "DEEPSEEK_API_KEY=from-file\n"
        "MOONSHOT_API_KEY=moonshot-file\n"
        "MEM0_API_KEY=mem0-file\n"
        "AWESOME_WEB_PROXY_URL=https://proxy-file.example\n",
        encoding="utf-8",
    )

    loaded = load_config_sources(
        paths=AwesomePaths.from_home(home),
        workspace=tmp_path / "workspace",
        workspace_trusted=False,
        environ={
            "DEEPSEEK_API_KEY": "from-process",
            "TAVILY_API_KEY": "tavily-process",
        },
    )

    assert loaded.secrets.deepseek_api_key is not None
    assert loaded.secrets.deepseek_api_key.get_secret_value() == "from-process"
    assert loaded.secrets.moonshot_api_key is not None
    assert loaded.secrets.mem0_api_key is not None
    assert loaded.secrets.tavily_api_key is not None
    assert loaded.secrets.tavily_api_key.get_secret_value() == "tavily-process"
    assert loaded.secrets.web_proxy_url is not None
    assert (
        loaded.secrets.web_proxy_url.get_secret_value() == "https://proxy-file.example"
    )
    assert loaded.secret_status.model_dump(mode="json") == {
        "deepseek_api_key": True,
        "moonshot_api_key": True,
        "mem0_api_key": True,
    }
    rendered = repr(loaded)
    assert "from-process" not in rendered
    assert "moonshot-file" not in rendered
    assert "mem0-file" not in rendered
    assert "tavily-process" not in rendered
    assert "proxy-file.example" not in rendered
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


def test_user_dotenv_hardlink_is_rejected_without_reading_external_secret(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    external = tmp_path / "external-sentinel"
    external.write_text("DEEPSEEK_API_KEY=external-secret\n", encoding="utf-8")
    os.link(external, home / ".env")

    with pytest.raises(ConfigurationInvalid) as raised:
        load_config_sources(
            paths=AwesomePaths.from_home(home),
            workspace=tmp_path / "workspace",
            workspace_trusted=False,
            environ={},
        )

    assert raised.value.code == "provider_secret_file_unsafe"
    assert "external-secret" not in str(raised.value)
    assert external.read_text(encoding="utf-8") == (
        "DEEPSEEK_API_KEY=external-secret\n"
    )


def test_user_dotenv_with_nul_is_rejected_before_parsing(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / ".env").write_bytes(b"MEM0_API_KEY=must-not-load\0suffix\n")

    with pytest.raises(ConfigurationInvalid) as raised:
        load_config_sources(
            paths=AwesomePaths.from_home(home),
            workspace=tmp_path / "workspace",
            workspace_trusted=False,
            environ={},
        )

    assert raised.value.code == "provider_secret_file_unsafe"
    assert "must-not-load" not in str(raised.value)


@pytest.mark.asyncio
async def test_config_loader_does_not_wait_on_dotenv_mutation_lock(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    env_file = home / ".env"
    env_file.write_text("DEEPSEEK_API_KEY=managed-secret\n", encoding="utf-8")
    entered = threading.Event()
    release = threading.Event()

    def hold_mutation_lock() -> None:
        with exclusive_resource_lock(env_file):
            entered.set()
            assert release.wait(1.0)

    holder = threading.Thread(target=hold_mutation_lock, daemon=True)
    holder.start()
    assert await asyncio.to_thread(entered.wait, 1.0)
    started = time.monotonic()
    try:
        loaded = load_config_sources(
            paths=AwesomePaths.from_home(home),
            workspace=tmp_path / "workspace",
            workspace_trusted=False,
            environ={},
        )
    finally:
        release.set()
        await asyncio.to_thread(holder.join, 1.0)

    assert time.monotonic() - started < 0.2
    assert loaded.provider_credentials.deepseek.awesome_configured is True
