import pytest

from awesome_agent.core.tools.permissions import (
    PermissionMode,
    PermissionPolicy,
    PermissionSession,
    PolicyAction,
    PolicyRequest,
    ToolCapability,
)


@pytest.mark.parametrize(
    ("capability", "expected"),
    [
        (ToolCapability.CONTEXT_READ, PolicyAction.ALLOW),
        (ToolCapability.WORKSPACE_READ, PolicyAction.ALLOW),
        (ToolCapability.WORKSPACE_WRITE, PolicyAction.ASK),
        (ToolCapability.WORKSPACE_DELETE, PolicyAction.ASK),
        (ToolCapability.SHELL_EXECUTE, PolicyAction.ASK),
        (ToolCapability.NETWORK_READ, PolicyAction.ASK),
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
        ToolCapability.CONTEXT_READ,
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


@pytest.mark.parametrize("mode", list(PermissionMode))
def test_network_read_always_asks_without_exact_thread_grant(
    mode: PermissionMode,
) -> None:
    decision = PermissionPolicy().evaluate(
        PolicyRequest(
            capability=ToolCapability.NETWORK_READ,
            mode=mode,
            thread_id="thread_current",
            granted_capabilities=frozenset({ToolCapability.NETWORK_READ}),
            granted_thread_capabilities=frozenset(
                {("thread_other", ToolCapability.NETWORK_READ.value)}
            ),
        )
    )

    assert decision.action is PolicyAction.ASK


@pytest.mark.parametrize("mode", list(PermissionMode))
def test_network_read_allows_exact_current_thread_grant(
    mode: PermissionMode,
) -> None:
    decision = PermissionPolicy().evaluate(
        PolicyRequest(
            capability=ToolCapability.NETWORK_READ,
            mode=mode,
            thread_id="thread_current",
            granted_thread_capabilities=frozenset(
                {("thread_current", ToolCapability.NETWORK_READ.value)}
            ),
        )
    )

    assert decision.action is PolicyAction.ALLOW


@pytest.mark.parametrize(
    ("capability", "expected"),
    [
        (ToolCapability.CONTEXT_READ, PolicyAction.ALLOW),
        (ToolCapability.WORKSPACE_READ, PolicyAction.ALLOW),
        (ToolCapability.WORKSPACE_WRITE, PolicyAction.ALLOW),
        (ToolCapability.WORKSPACE_DELETE, PolicyAction.ASK),
        (ToolCapability.SHELL_EXECUTE, PolicyAction.ASK),
        (ToolCapability.NETWORK_READ, PolicyAction.ASK),
    ],
)
def test_accept_edits_only_allows_workspace_creation_and_modification(
    capability: ToolCapability,
    expected: PolicyAction,
) -> None:
    decision = PermissionPolicy().evaluate(
        PolicyRequest(capability=capability, mode=PermissionMode.ACCEPT_EDITS)
    )

    assert decision.action is expected


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
    list(PermissionMode),
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


@pytest.mark.parametrize("mode", list(PermissionMode))
def test_context_read_always_allows_after_hard_admission(mode: PermissionMode) -> None:
    decision = PermissionPolicy().evaluate(
        PolicyRequest(capability=ToolCapability.CONTEXT_READ, mode=mode)
    )

    assert decision.action is PolicyAction.ALLOW


def test_mode_transition_clears_grants_and_advances_generation() -> None:
    session = PermissionSession()
    session.grant_thread_writes()
    session.grant_thread_network("thread_1")

    session.set_mode(PermissionMode.ACCEPT_EDITS)

    assert session.mode is PermissionMode.ACCEPT_EDITS
    assert session.granted_capabilities == set()
    assert session.thread_granted_capabilities == frozenset()
    assert session.generation == 1


def test_thread_network_grants_are_bound_and_explicitly_revocable() -> None:
    session = PermissionSession()
    session.grant_thread_network("thread_1")
    session.grant_thread_network("thread_2")

    assert session.thread_granted_capabilities == frozenset(
        {
            ("thread_1", ToolCapability.NETWORK_READ.value),
            ("thread_2", ToolCapability.NETWORK_READ.value),
        }
    )

    session.revoke_thread_network("thread_1")

    assert session.thread_granted_capabilities == frozenset(
        {("thread_2", ToolCapability.NETWORK_READ.value)}
    )

    session.revoke_thread_network()

    assert session.thread_granted_capabilities == frozenset()
