import pytest

from awesome_agent.core.tools.permissions import (
    PermissionMode,
    PermissionPolicy,
    PolicyAction,
    PolicyRequest,
    ToolCapability,
)


@pytest.mark.parametrize(
    ("capability", "expected"),
    [
        (ToolCapability.WORKSPACE_READ, PolicyAction.ALLOW),
        (ToolCapability.WORKSPACE_WRITE, PolicyAction.ASK),
        (ToolCapability.WORKSPACE_DELETE, PolicyAction.ASK),
        (ToolCapability.SHELL_EXECUTE, PolicyAction.ASK),
    ],
)
def test_request_approval_policy_table(
    capability: ToolCapability,
    expected: PolicyAction,
) -> None:
    decision = PermissionPolicy().evaluate(
        PolicyRequest(
            capability=capability,
            mode=PermissionMode.REQUEST_APPROVAL,
        )
    )

    assert decision.action is expected


@pytest.mark.parametrize(
    "capability",
    [
        ToolCapability.WORKSPACE_READ,
        ToolCapability.WORKSPACE_WRITE,
        ToolCapability.WORKSPACE_DELETE,
        ToolCapability.SHELL_EXECUTE,
    ],
)
def test_full_access_allows_known_capabilities(capability: ToolCapability) -> None:
    decision = PermissionPolicy().evaluate(
        PolicyRequest(capability=capability, mode=PermissionMode.FULL_ACCESS)
    )

    assert decision.action is PolicyAction.ALLOW


def test_thread_write_grant_only_allows_workspace_writes() -> None:
    policy = PermissionPolicy()

    write = policy.evaluate(
        PolicyRequest(
            capability=ToolCapability.WORKSPACE_WRITE,
            mode=PermissionMode.REQUEST_APPROVAL,
            granted_capabilities=frozenset({ToolCapability.WORKSPACE_WRITE}),
        )
    )
    delete = policy.evaluate(
        PolicyRequest(
            capability=ToolCapability.WORKSPACE_DELETE,
            mode=PermissionMode.REQUEST_APPROVAL,
            granted_capabilities=frozenset({ToolCapability.WORKSPACE_WRITE}),
        )
    )
    shell = policy.evaluate(
        PolicyRequest(
            capability=ToolCapability.SHELL_EXECUTE,
            mode=PermissionMode.REQUEST_APPROVAL,
            granted_capabilities=frozenset({ToolCapability.WORKSPACE_WRITE}),
        )
    )

    assert write.action is PolicyAction.ALLOW
    assert delete.action is PolicyAction.ASK
    assert shell.action is PolicyAction.ASK


@pytest.mark.parametrize(
    "mode",
    [PermissionMode.REQUEST_APPROVAL, PermissionMode.FULL_ACCESS],
)
def test_unknown_extension_capability_is_never_implicitly_allowed(
    mode: PermissionMode,
) -> None:
    decision = PermissionPolicy().evaluate(
        PolicyRequest(capability="mcp.custom", mode=mode)
    )

    assert decision.action is PolicyAction.ASK


@pytest.mark.parametrize("capability", ["memory.read", "memory.write"])
def test_builtin_memory_defers_to_its_own_explicit_user_policy(
    capability: str,
) -> None:
    decision = PermissionPolicy().evaluate(
        PolicyRequest(
            capability=capability,
            mode=PermissionMode.REQUEST_APPROVAL,
        )
    )

    assert decision.action is PolicyAction.ALLOW


@pytest.mark.parametrize(
    "reason",
    [
        "Sensitive workspace paths are protected.",
        "Privilege elevation commands are not allowed.",
    ],
)
def test_hard_denial_wins_in_every_permission_mode(reason: str) -> None:
    for mode in PermissionMode:
        decision = PermissionPolicy().evaluate(
            PolicyRequest(
                capability=ToolCapability.SHELL_EXECUTE,
                mode=mode,
                hard_deny_reason=reason,
            )
        )

        assert decision.action is PolicyAction.DENY
        assert decision.reason == reason
