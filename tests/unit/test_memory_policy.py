from awesome_agent.memory.models import MemoryAddRequest, MemoryTarget
from awesome_agent.memory.policy import MemoryPolicy


def _request(
    content: str,
    *,
    source: str = "explicit_user_request",
) -> MemoryAddRequest:
    return MemoryAddRequest(
        target=MemoryTarget.USER,
        content=content,
        source=source,
    )


def test_policy_allows_stable_preferences() -> None:
    decision = MemoryPolicy().evaluate(
        _request("Prefer short direct engineering updates.")
    )

    assert decision.action == "allow"
    assert decision.sanitized_content == "Prefer short direct engineering updates."


def test_policy_rejects_secrets_code_and_one_off_state() -> None:
    policy = MemoryPolicy()

    assert policy.evaluate(_request("OPENAI_API_KEY=sk-secret-value")).action == (
        "reject"
    )
    assert policy.evaluate(_request("```python\nprint('raw source')\n```")).action == (
        "reject"
    )
    assert (
        policy.evaluate(_request("Remember that this current test failed once")).action
        == "reject"
    )


def test_model_initiated_memory_requires_clear_stable_fact() -> None:
    policy = MemoryPolicy()

    vague = policy.evaluate(
        _request("The user may prefer dashboards.", source="model_initiated")
    )
    clear = policy.evaluate(
        _request(
            "User prefers concise engineering updates.",
            source="model_initiated",
        )
    )

    assert vague.action == "reject"
    assert clear.action == "allow"
