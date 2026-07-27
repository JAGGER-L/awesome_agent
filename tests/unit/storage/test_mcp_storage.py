from pathlib import Path

from awesome_agent.extensions.mcp import McpServerConfig, McpSource
from awesome_agent.storage import (
    APPLICATION_SCHEMA_VERSION,
    ApplicationSQLite,
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


async def test_store_uses_hash_bound_workspace_enablement(
    tmp_path: Path,
) -> None:
    database = ApplicationSQLite(tmp_path / "state.db")
    await database.initialize()
    try:
        store = SQLiteMcpEnablementStore(database)
        config = _workspace_config(args=("server.py", "--safe"), env=("TOKEN", "PATH"))

        await store.enable("workspace-key", config.id, mcp_config_hash(config))

        assert APPLICATION_SCHEMA_VERSION == 8
        enabled = await store.snapshot("workspace-key")
        assert enabled == {config.id: mcp_config_hash(config)}
        assert enabled[config.id] != mcp_config_hash(
            config.model_copy(update={"args": ("server.py", "--new")})
        )
        await store.disable("workspace-key", config.id)
        assert await store.snapshot("workspace-key") == {}
    finally:
        await database.aclose()


def test_config_hash_orders_arguments_but_normalizes_environment_names() -> None:
    first = _workspace_config(args=("a", "b"), env=("TOKEN", "PATH"))
    same = _workspace_config(args=("a", "b"), env=("PATH", "TOKEN"))
    changed = _workspace_config(args=("b", "a"), env=("PATH", "TOKEN"))

    assert mcp_config_hash(first) == mcp_config_hash(same)
    assert mcp_config_hash(first) != mcp_config_hash(changed)
