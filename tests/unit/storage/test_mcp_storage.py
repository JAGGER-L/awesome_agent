from pathlib import Path

from awesome_agent.extensions.mcp import McpServerConfig, McpSource
from awesome_agent.storage import (
    APPLICATION_SCHEMA_VERSION,
    SQLiteMcpEnablementStore,
    mcp_config_hash,
)


def _workspace_config(
    *, args: tuple[str, ...], env: tuple[str, ...]
) -> McpServerConfig:
    return McpServerConfig(
        id="project",
        command="python",
        args=args,
        env_names=env,
        source=McpSource.WORKSPACE,
    )


def test_migration_four_stores_only_hash_bound_workspace_enablement(
    tmp_path: Path,
) -> None:
    database = tmp_path / "state.db"
    store = SQLiteMcpEnablementStore(database)
    config = _workspace_config(args=("server.py", "--safe"), env=("TOKEN", "PATH"))

    store.enable("workspace-key", config.id, mcp_config_hash(config))

    assert APPLICATION_SCHEMA_VERSION == 5
    assert store.is_enabled("workspace-key", config.id, mcp_config_hash(config))
    assert not store.is_enabled(
        "workspace-key",
        config.id,
        mcp_config_hash(config.model_copy(update={"args": ("server.py", "--new")})),
    )
    store.disable("workspace-key", config.id)
    assert not store.is_enabled("workspace-key", config.id, mcp_config_hash(config))


def test_config_hash_orders_arguments_but_normalizes_environment_names() -> None:
    first = _workspace_config(args=("a", "b"), env=("TOKEN", "PATH"))
    same = _workspace_config(args=("a", "b"), env=("PATH", "TOKEN"))
    changed = _workspace_config(args=("b", "a"), env=("PATH", "TOKEN"))

    assert mcp_config_hash(first) == mcp_config_hash(same)
    assert mcp_config_hash(first) != mcp_config_hash(changed)
