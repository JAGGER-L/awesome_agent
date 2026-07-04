from pathlib import Path
from uuid import uuid4

import pytest

from awesome_agent.domain.enums import RiskLevel
from awesome_agent.memory.builtin import BuiltinMemoryStore
from awesome_agent.memory.external import NoopMemoryProvider
from awesome_agent.memory.policy import MemoryPolicy
from awesome_agent.memory.service import MemoryService
from awesome_agent.tools.approval import ApprovalPolicy
from awesome_agent.tools.executor import ToolExecutor
from awesome_agent.tools.memory import register_memory_tools
from awesome_agent.tools.models import ToolInvocation
from awesome_agent.tools.registry import ToolRegistry


def _executor(tmp_path: Path) -> ToolExecutor:
    service = MemoryService(
        builtin=BuiltinMemoryStore(root=tmp_path / "memory", policy=MemoryPolicy()),
        provider=NoopMemoryProvider(),
        builtin_enabled=True,
        provider_enabled=False,
    )
    registry = ToolRegistry()
    register_memory_tools(registry, service)
    return ToolExecutor(registry, ApprovalPolicy())


@pytest.mark.asyncio
async def test_memory_manage_add_list_delete(tmp_path: Path) -> None:
    executor = _executor(tmp_path)
    agent_id = uuid4()
    add = await executor.execute(
        ToolInvocation(
            tool_name="memory.manage",
            run_id=uuid4(),
            agent_id=agent_id,
            profile="leader",
            capabilities={"memory:manage"},
            arguments={
                "action": "add",
                "target": "user",
                "content": "Prefer concise engineering updates.",
                "source": "explicit_user_request",
            },
        )
    )
    memory_id = add.output["memory_id"]

    listed = await executor.execute(
        ToolInvocation(
            tool_name="memory.manage",
            run_id=uuid4(),
            agent_id=agent_id,
            profile="leader",
            capabilities={"memory:manage"},
            arguments={"action": "list", "target": "user"},
        )
    )
    deleted = await executor.execute(
        ToolInvocation(
            tool_name="memory.manage",
            run_id=uuid4(),
            agent_id=agent_id,
            profile="leader",
            capabilities={"memory:manage"},
            arguments={
                "action": "delete",
                "target": "user",
                "memory_id": memory_id,
            },
        )
    )

    assert add.output["status"] == "added"
    assert listed.output["entries"][0]["id"] == memory_id
    assert deleted.output["status"] == "deleted"


def test_memory_tool_spec_is_low_risk_and_unsandboxed(tmp_path: Path) -> None:
    service = MemoryService(
        builtin=BuiltinMemoryStore(root=tmp_path / "memory", policy=MemoryPolicy()),
        provider=NoopMemoryProvider(),
        builtin_enabled=True,
        provider_enabled=False,
    )
    registry = ToolRegistry()

    register_memory_tools(registry, service)

    spec, _handler = registry.resolve("memory.manage")
    assert spec.risk_level is RiskLevel.LOW
    assert spec.sandbox_required is False
    assert spec.required_capabilities == {"memory:manage"}
