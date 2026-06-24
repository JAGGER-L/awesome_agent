from awesome_agent.agents.profiles import RoleModelResolver, default_profiles
from awesome_agent.domain.enums import AgentKind


def _resolver(overrides: dict[str, str] | None = None) -> RoleModelResolver:
    return RoleModelResolver(
        leader_model="deepseek-v4-pro",
        teammate_model="deepseek-v4-flash",
        verifier_model="deepseek-v4-flash",
        subagent_model="deepseek-v4-flash",
        role_overrides=overrides,
    )


def test_default_role_models() -> None:
    resolver = _resolver()

    assert (
        resolver.resolve(kind=AgentKind.LEADER, profile="leader") == "deepseek-v4-pro"
    )
    for profile in default_profiles():
        assert (
            resolver.resolve(kind=profile.kind, profile=profile.name)
            == "deepseek-v4-flash"
        )
    assert (
        resolver.resolve(kind=AgentKind.SUBAGENT, profile="repo-explorer")
        == "deepseek-v4-flash"
    )


def test_role_model_override_wins() -> None:
    resolver = _resolver({"backend-engineer": "deepseek-v4-pro"})

    assert (
        resolver.resolve(
            kind=AgentKind.TEAMMATE,
            profile="backend-engineer",
        )
        == "deepseek-v4-pro"
    )
