import pytest

from awesome_agent.memory.models import MemoryPolicyStatus, MemoryScope
from awesome_agent.memory.policy import CloudMemoryPolicy, cloud_fact_hash


@pytest.mark.parametrize(
    ("scope", "content"),
    [
        (MemoryScope.USER, "User prefers concise architecture explanations."),
        (MemoryScope.WORKSPACE, "Project uses pytest for focused validation."),
    ],
)
def test_accepts_stable_opaque_cloud_facts(
    scope: MemoryScope,
    content: str,
) -> None:
    result = CloudMemoryPolicy().evaluate(
        content,
        scope=scope,
        workspace_key="ws_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    )

    assert result.status is MemoryPolicyStatus.ELIGIBLE
    assert result.candidate is not None
    assert result.candidate.content == content
    assert len(result.candidate.fact_hash) == 64


@pytest.mark.parametrize(
    ("content", "code"),
    [
        ("token=super-secret-value", "secret_like_content"),
        ("Private file is /Users/alice/work/notes.txt", "absolute_private_path"),
        ("Repository awesome_agent uses Python.", "repository_identifier"),
        ("Git remote is git@github.com:private/repo.git", "repository_identifier"),
        ("```python\nprint('source')\n```", "raw_code_or_output"),
        ("diff --git a/a.py b/a.py\n+change", "raw_code_or_output"),
        ("tool output: pytest failed", "raw_code_or_output"),
        ("Current task is blocked on a test failure.", "transient_task_state"),
        ("Completed this issue today.", "transient_task_state"),
        ("x" * 501, "too_large"),
    ],
)
def test_rejects_private_transient_or_raw_cloud_payloads(
    content: str,
    code: str,
) -> None:
    result = CloudMemoryPolicy().evaluate(
        content,
        scope=MemoryScope.USER,
        workspace_key="ws_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    )

    assert result.status is MemoryPolicyStatus.REJECTED
    assert result.error_code == code
    assert result.candidate is None
    assert "super-secret-value" not in result.model_dump_json()


def test_unsupported_scope_and_workspace_authority_are_rejected() -> None:
    policy = CloudMemoryPolicy()

    unsupported = policy.evaluate(
        "Stable fact",
        scope="global",
        workspace_key="ws_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    )
    missing_workspace = policy.evaluate(
        "Project uses pytest.",
        scope=MemoryScope.WORKSPACE,
        workspace_key=None,
    )

    assert unsupported.error_code == "unsupported_scope"
    assert missing_workspace.error_code == "workspace_scope_missing"


def test_fact_hash_normalizes_unicode_whitespace_case_and_scope() -> None:
    workspace = "ws_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    first = cloud_fact_hash(
        " Café   PREFERS pytest ",
        scope=MemoryScope.WORKSPACE,
        workspace_key=workspace,
    )
    same = cloud_fact_hash(
        "cafe\u0301 prefers PYTEST",
        scope=MemoryScope.WORKSPACE,
        workspace_key=workspace,
    )
    user = cloud_fact_hash(
        "café prefers pytest",
        scope=MemoryScope.USER,
        workspace_key=None,
    )
    other_workspace = cloud_fact_hash(
        "café prefers pytest",
        scope=MemoryScope.WORKSPACE,
        workspace_key="ws_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    )

    assert first == same
    assert first != user
    assert first != other_workspace
