from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import ClassVar, cast

import pytest

from awesome_agent.application import composition
from awesome_agent.application.contracts import ProductErrorCode
from awesome_agent.application.facade import LocalApplication
from awesome_agent.application.turns import TurnCoordinator
from awesome_agent.config import BudgetConfig, TurnConfig
from awesome_agent.conversation import TurnStatus
from awesome_agent.core.events import CollectingEventSink
from awesome_agent.core.workspace import WorkspaceTrustService, resolve_workspace
from awesome_agent.extensions.mcp import McpServerConfig, McpServerStatus
from awesome_agent.extensions.skills import SkillCatalog, discover_skills
from awesome_agent.storage import StateLease, StateLeaseMode, StateLeaseUnavailable
from awesome_agent.storage.trust import SQLiteWorkspaceTrustStore


async def _trusted_application(
    tmp_path: Path,
    *,
    environ: dict[str, str] | None = None,
) -> tuple[LocalApplication, composition._LocalApplicationBackend]:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    WorkspaceTrustService(
        SQLiteWorkspaceTrustStore(home / "state" / "application.db")
    ).accept(resolve_workspace(workspace))
    application = await composition.compose_local_application(
        home=home,
        workspace=workspace,
        event_sink=CollectingEventSink(),
        environ=environ or {},
    )
    backend = cast(composition._LocalApplicationBackend, application._backend)
    return application, backend


@pytest.mark.asyncio
async def test_same_workspace_runtime_lease_prevents_live_turn_recovery(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    identity = resolve_workspace(workspace)
    WorkspaceTrustService(
        SQLiteWorkspaceTrustStore(home / "state" / "application.db")
    ).accept(identity)
    first = await composition.compose_local_application(
        home=home,
        workspace=workspace,
        event_sink=CollectingEventSink(),
        environ={},
    )
    first_ready = await first.initialize()
    assert first_ready.ok is True
    first_backend = cast(composition._LocalApplicationBackend, first._backend)
    thread = first_backend._conversation.create_thread(identity.key)
    turn = first_backend._conversation.begin_turn(
        thread.id,
        "still running",
        TurnConfig(
            provider="deepseek",
            model="deepseek/deepseek-v4-flash",
            budgets=BudgetConfig(),
        ),
        client_message_id="client_live_turn",
    )

    second = await composition.compose_local_application(
        home=home,
        workspace=workspace,
        event_sink=CollectingEventSink(),
        environ={},
    )
    blocked = await second.initialize()

    assert blocked.ok is False
    assert blocked.error is not None
    assert blocked.error.code is ProductErrorCode.OPERATION_BUSY
    observed = first_backend._conversation.read_thread(thread.id).turns[0]
    assert (observed.id, observed.status) == (turn.id, TurnStatus.IN_PROGRESS)

    await first.shutdown()
    recovered = await second.initialize()
    assert recovered.ok is True
    second_backend = cast(composition._LocalApplicationBackend, second._backend)
    assert (
        second_backend._conversation.read_thread(thread.id).turns[0].status
        is TurnStatus.FAILED
    )
    await second.shutdown()


@pytest.mark.asyncio
async def test_workspace_runtime_lease_uses_filesystem_identity_across_path_aliases(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    alias = Path(f"\\\\?\\{workspace}") if os.name == "nt" else workspace / "."
    direct_identity = resolve_workspace(workspace)
    alias_identity = resolve_workspace(alias)
    assert alias_identity.root_identity == direct_identity.root_identity
    if os.name == "nt":
        assert alias_identity.key != direct_identity.key
    trust = WorkspaceTrustService(
        SQLiteWorkspaceTrustStore(home / "state" / "application.db")
    )
    trust.accept(direct_identity)
    trust.accept(alias_identity)
    first = await composition.compose_local_application(
        home=home,
        workspace=workspace,
        event_sink=CollectingEventSink(),
        environ={},
    )
    second = await composition.compose_local_application(
        home=home,
        workspace=alias,
        event_sink=CollectingEventSink(),
        environ={},
    )

    assert (await first.initialize()).ok is True
    blocked = await second.initialize()

    assert blocked.ok is False
    assert blocked.error is not None
    assert blocked.error.code is ProductErrorCode.OPERATION_BUSY
    await first.shutdown()
    assert (await second.initialize()).ok is True
    await second.shutdown()


@pytest.mark.asyncio
async def test_workspace_runtime_path_lease_survives_root_replacement(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    trust = WorkspaceTrustService(
        SQLiteWorkspaceTrustStore(home / "state" / "application.db")
    )
    trust.accept(resolve_workspace(workspace))
    first = await composition.compose_local_application(
        home=home,
        workspace=workspace,
        event_sink=CollectingEventSink(),
        environ={},
    )
    assert (await first.initialize()).ok is True
    original = tmp_path / "workspace-original"
    workspace.rename(original)
    workspace.mkdir()
    second = await composition.compose_local_application(
        home=home,
        workspace=workspace,
        event_sink=CollectingEventSink(),
        environ={},
    )

    blocked = await second.initialize()

    assert blocked.ok is False
    assert blocked.error is not None
    assert blocked.error.code is ProductErrorCode.OPERATION_BUSY
    await first.shutdown()
    assert (await second.initialize()).ok is True
    await second.shutdown()


@pytest.mark.asyncio
async def test_entity_lease_failure_releases_already_acquired_path_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application, _ = await _trusted_application(tmp_path)
    original_acquire = StateLease.acquire

    def fail_entity_lease(
        cls: type[StateLease],
        home: Path,
        mode: StateLeaseMode,
    ) -> StateLease:
        del cls
        if ".workspace-entity-leases" in home.parts:
            raise StateLeaseUnavailable(home, mode)
        return original_acquire(home, mode)

    monkeypatch.setattr(StateLease, "acquire", classmethod(fail_entity_lease))

    blocked = await application.initialize()

    assert blocked.ok is False
    assert blocked.error is not None
    assert blocked.error.code is ProductErrorCode.OPERATION_BUSY
    monkeypatch.setattr(StateLease, "acquire", original_acquire)
    assert (await application.initialize()).ok is True
    await application.shutdown()


@pytest.mark.asyncio
async def test_activation_passes_union_of_disabled_skills_to_discovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.yaml").write_text(
        "skills:\n  disabled: [user_only, shared]\n",
        encoding="utf-8",
    )
    workspace = tmp_path / "workspace"
    (workspace / ".awesome").mkdir(parents=True)
    (workspace / ".awesome" / "config.yaml").write_text(
        "skills:\n  disabled: [workspace_only, shared]\n",
        encoding="utf-8",
    )
    WorkspaceTrustService(
        SQLiteWorkspaceTrustStore(home / "state" / "application.db")
    ).accept(resolve_workspace(workspace))
    discovered: list[set[str]] = []

    def capture_discovery(
        *,
        bundled_root: Path | None,
        user_root: Path | None,
        workspace_root: Path | None,
        workspace_trusted: bool,
        workspace_anchor: Path | None = None,
        disabled: set[str] | None = None,
    ) -> SkillCatalog:
        discovered.append(set(disabled or ()))
        return discover_skills(
            bundled_root=bundled_root,
            user_root=user_root,
            workspace_root=workspace_root,
            workspace_trusted=workspace_trusted,
            workspace_anchor=workspace_anchor,
            disabled=disabled,
        )

    monkeypatch.setattr(composition, "discover_skills", capture_discovery)
    application = await composition.compose_local_application(
        home=home,
        workspace=workspace,
        event_sink=CollectingEventSink(),
        environ={},
    )

    initialized = await application.initialize()

    assert initialized.ok is True
    assert discovered == [{"user_only", "workspace_only", "shared"}]
    await application.shutdown()


@pytest.mark.asyncio
async def test_selected_model_context_limit_clamps_turn_and_context_budgets(
    tmp_path: Path,
) -> None:
    application, backend = await _trusted_application(
        tmp_path,
        environ={"DEEPSEEK_API_KEY": "test-key"},
    )
    backend._paths.config_file.parent.mkdir(parents=True, exist_ok=True)
    backend._paths.config_file.write_text(
        "providers:\n"
        "  default_model: deepseek/deepseek-v4-flash\n"
        "budgets:\n"
        "  total_context_tokens: 999999\n",
        encoding="utf-8",
    )

    initialized = await application.initialize()

    assert initialized.ok is True
    context = backend._context
    assert context is not None
    assert context._configured_total_tokens == 999_999
    assert context._model_context_limit == 262_144
    thread = backend._conversation.create_thread(
        backend._workspace.key,
        current_model="deepseek/deepseek-v4-flash",
    )
    assert backend._turn_config(thread).budgets.total_context_tokens == 262_144
    await application.shutdown()


class _TrackingMcpManager:
    instances: ClassVar[list[_TrackingMcpManager]] = []
    hang_first_close: ClassVar[bool] = False

    def __init__(self, **kwargs: object) -> None:
        del kwargs
        self.close_calls = 0
        self.close_cancelled = False
        self._index = len(self.instances)
        self.instances.append(self)

    def configs(self) -> tuple[McpServerConfig, ...]:
        return ()

    def statuses(self) -> tuple[McpServerStatus, ...]:
        return ()

    async def start_enabled(self) -> None:
        return None

    async def aclose(self) -> None:
        self.close_calls += 1
        if self.hang_first_close and self._index == 0:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.close_cancelled = True
                raise


_ROLLED_BACK_FIELDS = (
    "_commands",
    "_command_dispatcher",
    "_diagnostic_commands",
    "_change_commands",
    "_permission_commands",
    "_provider_configuration",
    "_turns",
    "_direct",
    "_extensions",
    "_context",
    "_registry",
    "_local_memory",
    "_mem0_adapter",
    "_mem0_session",
    "_mcp",
    "_change_scope",
    "_change_store",
    "_change_analyzer",
    "_change_operations",
    "_workspace_instruction_snapshot",
)


def _assert_activation_rolled_back(
    backend: composition._LocalApplicationBackend,
) -> None:
    assert backend._initialized is False
    assert all(getattr(backend, name) is None for name in _ROLLED_BACK_FIELDS)


@pytest.mark.asyncio
async def test_activation_failure_closes_candidate_and_retry_does_not_leak(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _TrackingMcpManager.instances = []
    _TrackingMcpManager.hang_first_close = False
    attempts = 0

    async def fail_once(self: TurnCoordinator) -> tuple[object, ...]:
        del self
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("reconcile failed")
        return ()

    monkeypatch.setattr(composition, "McpManager", _TrackingMcpManager)
    monkeypatch.setattr(TurnCoordinator, "reconcile_startup", fail_once)
    application, backend = await _trusted_application(tmp_path)

    with pytest.raises(RuntimeError, match="reconcile failed"):
        await application.initialize()

    first = _TrackingMcpManager.instances[0]
    assert first.close_calls == 1
    _assert_activation_rolled_back(backend)

    initialized = await application.initialize()

    assert initialized.ok is True
    assert len(_TrackingMcpManager.instances) == 2
    assert first.close_calls == 1
    second = _TrackingMcpManager.instances[1]
    assert second.close_calls == 0
    await application.shutdown()
    assert second.close_calls == 1


@pytest.mark.asyncio
async def test_cancelled_activation_bounds_candidate_cleanup_and_rolls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _TrackingMcpManager.instances = []
    _TrackingMcpManager.hang_first_close = True
    entered = asyncio.Event()

    async def block_reconcile(self: TurnCoordinator) -> tuple[object, ...]:
        del self
        entered.set()
        await asyncio.Event().wait()
        return ()

    monkeypatch.setattr(composition, "McpManager", _TrackingMcpManager)
    monkeypatch.setattr(TurnCoordinator, "reconcile_startup", block_reconcile)
    monkeypatch.setattr(composition, "_ACTIVATION_ROLLBACK_TIMEOUT_SECONDS", 0.01)
    application, backend = await _trusted_application(tmp_path)
    initializing = asyncio.create_task(application.initialize())
    await asyncio.wait_for(entered.wait(), timeout=1)

    initializing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(initializing, timeout=0.5)

    candidate = _TrackingMcpManager.instances[0]
    assert candidate.close_calls == 1
    assert candidate.close_cancelled is True
    _assert_activation_rolled_back(backend)
    await application.shutdown()
