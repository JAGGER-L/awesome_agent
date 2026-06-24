from pydantic import BaseModel

from awesome_agent.domain.enums import AgentKind


class AgentProfile(BaseModel):
    name: str
    kind: AgentKind = AgentKind.TEAMMATE
    can_write: bool = False
    can_delegate: bool = True
    is_verifier: bool = False


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
