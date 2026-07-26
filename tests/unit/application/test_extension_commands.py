import asyncio
from typing import cast

import pytest

from awesome_agent.application.extension_commands import _finish_mcp_enablement
from awesome_agent.extensions.mcp import (
    McpConnectionState,
    McpManager,
    McpServerStatus,
)


class _RecordingMcpManager:
    def __init__(self, *, fail_publish: bool = False) -> None:
        self.enablements: dict[str, str] = {}
        self.published: list[tuple[str, str | None]] = []
        self.refresh_calls = 0
        self.fail_publish = fail_publish

    def publish_enablement(
        self,
        server_id: str,
        config_hash: str | None,
    ) -> None:
        if self.fail_publish:
            raise RuntimeError("enablement publication failed")
        self.published.append((server_id, config_hash))
        if config_hash is None:
            self.enablements.pop(server_id, None)
        else:
            self.enablements[server_id] = config_hash

    async def refresh_enablement(self, server_id: str) -> McpServerStatus:
        self.refresh_calls += 1
        return McpServerStatus(
            server_id=server_id,
            state=McpConnectionState.CONFIGURED,
        )


@pytest.mark.asyncio
async def test_mcp_enablement_finishes_once_and_preserves_first_cancellation() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    durable_snapshot: dict[str, str] = {}
    manager = _RecordingMcpManager()

    async def persist() -> None:
        entered.set()
        await release.wait()
        durable_snapshot["fixture"] = "hash_1"

    running = asyncio.create_task(
        _finish_mcp_enablement(
            persist(),
            manager=cast(McpManager, manager),
            server_id="fixture",
            config_hash="hash_1",
        )
    )
    await entered.wait()

    running.cancel("first cancellation")
    await asyncio.sleep(0)
    running.cancel("second cancellation")
    release.set()

    with pytest.raises(asyncio.CancelledError) as cancelled:
        await running

    assert cancelled.value.args == ("first cancellation",)
    assert durable_snapshot == {"fixture": "hash_1"}
    assert manager.enablements == durable_snapshot
    assert manager.published == [("fixture", "hash_1")]
    assert manager.refresh_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_stage", ["persist", "publish"])
async def test_mcp_enablement_late_failure_preserves_first_cancellation(
    failure_stage: str,
) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    durable_snapshot: dict[str, str] = {}
    manager = _RecordingMcpManager(fail_publish=failure_stage == "publish")

    async def persist() -> None:
        entered.set()
        await release.wait()
        if failure_stage == "persist":
            raise RuntimeError("enablement persistence failed")
        durable_snapshot["fixture"] = "hash_1"

    running = asyncio.create_task(
        _finish_mcp_enablement(
            persist(),
            manager=cast(McpManager, manager),
            server_id="fixture",
            config_hash="hash_1",
        )
    )
    await entered.wait()
    running.cancel("first cancellation")
    await asyncio.sleep(0)
    running.cancel("second cancellation")
    release.set()

    with pytest.raises(asyncio.CancelledError) as cancelled:
        await running

    assert cancelled.value.args == ("first cancellation",)
    assert manager.refresh_calls == 0
    if failure_stage == "persist":
        assert durable_snapshot == {}
    else:
        assert durable_snapshot == {"fixture": "hash_1"}
    assert manager.enablements == {}
    assert manager.published == []
