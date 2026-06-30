import asyncio

from awesome_agent.domain.enums import ApprovalDecision
from awesome_agent.tools.approval import ApprovalPolicy
from awesome_agent.tools.models import (
    ApprovalRequired,
    ToolDenied,
    ToolInvocation,
    ToolResult,
)
from awesome_agent.tools.registry import ProgressCallback, ToolRegistry


class ToolExecutor:
    def __init__(self, registry: ToolRegistry, policy: ApprovalPolicy) -> None:
        self._registry = registry
        self._policy = policy

    async def execute(
        self,
        invocation: ToolInvocation,
        *,
        progress: ProgressCallback | None = None,
    ) -> ToolResult:
        spec, handler = self._registry.resolve(invocation.tool_name)
        if (
            invocation.effective_tool_names is not None
            and invocation.tool_name not in invocation.effective_tool_names
        ):
            raise ToolDenied(invocation.tool_name)
        if spec.allowed_profiles and invocation.profile not in spec.allowed_profiles:
            raise ToolDenied(invocation.tool_name)
        if not spec.required_capabilities.issubset(invocation.capabilities):
            raise ToolDenied(invocation.tool_name)

        outcome = self._policy.evaluate(spec, invocation)
        if outcome.decision is ApprovalDecision.DENY:
            raise ToolDenied(invocation.tool_name)
        if outcome.decision is ApprovalDecision.ASK and not invocation.approval_granted:
            raise ApprovalRequired(invocation)
        return await asyncio.wait_for(
            handler(invocation, progress),
            timeout=spec.timeout_seconds,
        )
