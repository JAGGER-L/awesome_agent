from awesome_agent.memory.builtin import BuiltinMemoryStore
from awesome_agent.memory.external import ExternalMemory
from awesome_agent.memory.models import MemoryCandidate
from awesome_agent.memory.policy import MemoryPolicy


class MemoryPipeline:
    def __init__(
        self,
        *,
        policy: MemoryPolicy,
        builtin: BuiltinMemoryStore,
        external: ExternalMemory | None,
        builtin_enabled: bool,
        external_enabled: bool,
    ) -> None:
        self._policy = policy
        self._builtin = builtin
        self._external = external
        self._builtin_enabled = builtin_enabled
        self._external_enabled = external_enabled

    async def process(
        self,
        candidate: MemoryCandidate,
        *,
        user_id: str,
        project_id: str,
    ) -> dict[str, bool]:
        if not self._policy.accept(candidate):
            return {"builtin": False, "external": False}

        builtin_written = (
            self._builtin.write(candidate) if self._builtin_enabled else False
        )
        external_written = False
        if self._external_enabled and self._external is not None:
            external_written = await self._external.add(
                candidate,
                user_id=user_id,
                project_id=project_id,
            )
        return {
            "builtin": builtin_written,
            "external": external_written,
        }
