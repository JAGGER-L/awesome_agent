from awesome_agent.core.changes import ChangeJournal, ChangeLifecycle
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

    def change_set_for_tool(
        self,
        *,
        tool_name: str,
        owner: str,
        turn_id: str | None,
    ) -> str | None:
        registered = self._registry.resolve(tool_name)
        if registered is None or registered.spec.read_only:
            return None
        return self.acquire(owner, turn_id=turn_id)

    def acquire(self, owner: str, *, turn_id: str | None) -> str:
        current = self._identifiers.get(owner)
        if current is not None:
            return current
        change_set = self._journal.begin(
            session_id=self._session_id,
            turn_id=turn_id,
            workspace=self._workspace,
        )
        self._identifiers[owner] = change_set.id
        return change_set.id

    def seal(self, owner: str) -> None:
        identifier = self._identifiers.pop(owner, None)
        if identifier is None:
            return
        change_set = self._store.get(identifier)
        if change_set is not None and change_set.lifecycle is ChangeLifecycle.OPEN:
            self._journal.seal(identifier)

    def reconcile(self) -> None:
        self._journal.reconcile_pending()
