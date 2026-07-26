import asyncio
from collections.abc import Awaitable

from awesome_agent.core.cancellation import finish_cancellation_safe
from awesome_agent.core.changes import ChangeJournal, ChangeLifecycle
from awesome_agent.core.changes.errors import ChangeLifecycleError
from awesome_agent.core.changes.ports import ChangeSetStore
from awesome_agent.core.tools.registry import ToolRegistry
from awesome_agent.core.workspace import WorkspaceIdentity


class ChangeScope:
    def __init__(
        self,
        *,
        journal: ChangeJournal,
        store: ChangeSetStore,
        registry: ToolRegistry,
        session_id: str,
        workspace: WorkspaceIdentity,
    ) -> None:
        self._journal = journal
        self._store = store
        self._registry = registry
        self._session_id = session_id
        self._workspace = workspace
        self._identifiers: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def change_set_for_tool(
        self,
        *,
        tool_name: str,
        owner: str,
        turn_id: str | None,
    ) -> str | None:
        registered = self._registry.resolve(tool_name)
        if registered is None or registered.spec.read_only:
            return None
        return await self.acquire(owner, turn_id=turn_id)

    async def acquire(self, owner: str, *, turn_id: str | None) -> str:
        async with self._lock:
            current = self._identifiers.get(owner)
            if current is not None:
                return current
            return await _finish_cancellation_safe(
                self._begin_and_publish(owner, turn_id=turn_id)
            )

    async def _begin_and_publish(self, owner: str, *, turn_id: str | None) -> str:
        change_set = await self._journal.begin(
            session_id=self._session_id,
            turn_id=turn_id,
            workspace=self._workspace,
        )
        self._identifiers[owner] = change_set.id
        return change_set.id

    async def seal(self, owner: str) -> None:
        async with self._lock:
            identifier = self._identifiers.get(owner)
            if identifier is None:
                return
            await _finish_cancellation_safe(
                self._seal_and_unpublish(owner, identifier=identifier)
            )

    async def _seal_and_unpublish(self, owner: str, *, identifier: str) -> None:
        change_set = await self._store.get(identifier)
        if change_set is not None and change_set.lifecycle is ChangeLifecycle.OPEN:
            await self._journal.reconcile_pending(change_set_id=identifier)
            reconciled = await self._store.get(identifier)
            if reconciled is not None and reconciled.lifecycle is ChangeLifecycle.OPEN:
                await self._journal.seal(identifier)
        self._identifiers.pop(owner, None)

    async def reconcile(self) -> None:
        async with self._lock:
            if self._identifiers:
                raise ChangeLifecycleError(
                    "Startup reconciliation cannot run with active ChangeSets."
                )
            await self._journal.reconcile_pending()
            await self._journal.seal_orphaned_open()


async def _finish_cancellation_safe[T](operation: Awaitable[T]) -> T:
    result, cancellation = await finish_cancellation_safe(operation)
    if cancellation is not None:
        raise cancellation
    return result
