import pytest
from tests.type_helpers import test_settings

from awesome_agent.agents.profiles import RoleModelResolver, default_profiles
from awesome_agent.domain.enums import AgentKind
from awesome_agent.modeling.catalog import ModelCatalogError


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


def test_role_model_resolver_from_settings_rejects_invalid_role_model() -> None:
    with pytest.raises(ModelCatalogError) as raised:
        RoleModelResolver.from_settings(test_settings(leader_model="gpt-4o"))

    assert raised.value.code == "invalid_role_model"
    assert "leader" in str(raised.value)


def test_role_model_resolver_from_settings_rejects_invalid_override() -> None:
    with pytest.raises(ModelCatalogError) as raised:
        RoleModelResolver.from_settings(
            test_settings(role_model_overrides={"reviewer": "gpt-4o"})
        )

    assert raised.value.code == "invalid_role_model"
    assert "reviewer" in str(raised.value)
