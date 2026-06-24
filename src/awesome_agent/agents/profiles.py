from __future__ import annotations

from pydantic import BaseModel

from awesome_agent.domain.enums import AgentKind
from awesome_agent.settings import Settings


class AgentProfile(BaseModel):
    name: str
    kind: AgentKind = AgentKind.TEAMMATE
    can_write: bool = False
    can_delegate: bool = True
    is_verifier: bool = False


class RoleModelResolver:
    def __init__(
        self,
        *,
        leader_model: str,
        teammate_model: str,
        verifier_model: str,
        subagent_model: str,
        role_overrides: dict[str, str] | None = None,
    ) -> None:
        self._leader_model = leader_model
        self._teammate_model = teammate_model
        self._verifier_model = verifier_model
        self._subagent_model = subagent_model
        self._role_overrides = role_overrides or {}

    def resolve(self, *, kind: AgentKind, profile: str) -> str:
        override = self._role_overrides.get(profile)
        if override is not None:
            return override
        if kind is AgentKind.LEADER:
            return self._leader_model
        if kind is AgentKind.VERIFIER:
            return self._verifier_model
        if kind is AgentKind.SUBAGENT:
            return self._subagent_model
        return self._teammate_model

    @classmethod
    def from_settings(cls, settings: Settings) -> RoleModelResolver:
        return cls(
            leader_model=settings.leader_model,
            teammate_model=settings.teammate_model,
            verifier_model=settings.verifier_model,
            subagent_model=settings.subagent_model,
            role_overrides=settings.role_model_overrides,
        )


class ProfileRegistry:
    def __init__(self, profiles: list[AgentProfile] | None = None) -> None:
        self._profiles: dict[str, AgentProfile] = {}
        for profile in profiles or default_profiles():
            self.register(profile)

    def register(self, profile: AgentProfile) -> None:
        if profile.name in self._profiles:
            raise ValueError(f"Profile already exists: {profile.name}")
        self._profiles[profile.name] = profile

    def get(self, name: str) -> AgentProfile:
        try:
            return self._profiles[name]
        except KeyError as error:
            raise KeyError(f"Unknown agent profile: {name}") from error


def default_profiles() -> list[AgentProfile]:
    writing = {
        "frontend-engineer",
        "backend-engineer",
        "database-engineer",
        "devops-engineer",
        "test-engineer",
        "documentation-engineer",
    }
    profiles = [
        AgentProfile(name=name, can_write=name in writing)
        for name in [
            "architect",
            "repo-explorer",
            "frontend-engineer",
            "backend-engineer",
            "database-engineer",
            "devops-engineer",
            "test-engineer",
            "security-engineer",
            "reviewer",
            "documentation-engineer",
        ]
    ]
    profiles.append(
        AgentProfile(
            name="verifier",
            kind=AgentKind.VERIFIER,
            can_write=False,
            can_delegate=True,
            is_verifier=True,
        )
    )
    return profiles
