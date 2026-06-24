from uuid import uuid4

import pytest

from awesome_agent.domain.enums import ApprovalDecision, RiskLevel
from awesome_agent.tools.approval import (
    ApprovalPolicy,
    CommandRule,
    default_command_policy,
)
from awesome_agent.tools.executor import ToolExecutor
from awesome_agent.tools.models import (
    ApprovalRequired,
    ToolDenied,
    ToolInvocation,
    ToolResult,
    ToolSpec,
)
from awesome_agent.tools.registry import ToolRegistry


async def _handler(invocation: ToolInvocation, progress: object) -> ToolResult:
    return ToolResult(invocation_id=invocation.id, output={"ok": True})


def _spec(risk: RiskLevel = RiskLevel.LOW) -> ToolSpec:
    return ToolSpec(
        name="shell",
        description="Run a command",
        risk_level=risk,
        required_capabilities={"shell"},
    )


def test_registry_rejects_duplicate_tool() -> None:
    registry = ToolRegistry()
    registry.register(_spec(), _handler)

    with pytest.raises(ValueError, match="already registered"):
        registry.register(_spec(), _handler)


@pytest.mark.asyncio
async def test_executor_requires_capability() -> None:
    registry = ToolRegistry()
    registry.register(_spec(), _handler)
    executor = ToolExecutor(registry, ApprovalPolicy())
    invocation = ToolInvocation(
        tool_name="shell",
        agent_id=uuid4(),
        profile="backend-engineer",
    )

    with pytest.raises(ToolDenied):
        await executor.execute(invocation)


@pytest.mark.asyncio
async def test_executor_requires_approval_for_matching_command() -> None:
    registry = ToolRegistry()
    registry.register(_spec(), _handler)
    policy = ApprovalPolicy(
        [
            CommandRule.build(
                "git push",
                ApprovalDecision.ASK,
                "push requires approval",
            )
        ]
    )
    executor = ToolExecutor(registry, policy)
    invocation = ToolInvocation(
        tool_name="shell",
        agent_id=uuid4(),
        profile="backend-engineer",
        capabilities={"shell"},
        arguments={"command": "git push"},
    )

    with pytest.raises(ApprovalRequired):
        await executor.execute(invocation)

    approved = invocation.model_copy(update={"approval_granted": True})
    result = await executor.execute(approved)
    assert result.output == {"ok": True}


@pytest.mark.asyncio
async def test_default_policy_denies_destructive_command() -> None:
    registry = ToolRegistry()
    registry.register(_spec(), _handler)
    executor = ToolExecutor(registry, default_command_policy())
    invocation = ToolInvocation(
        tool_name="shell",
        agent_id=uuid4(),
        profile="backend-engineer",
        capabilities={"shell"},
        arguments={"command": "rm -rf /"},
        approval_granted=True,
    )

    with pytest.raises(ToolDenied):
        await executor.execute(invocation)
