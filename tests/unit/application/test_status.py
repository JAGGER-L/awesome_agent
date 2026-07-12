from __future__ import annotations

from pathlib import Path

import pytest

from awesome_agent.application.commands import CommandIntent, CommandName, CommandStatus
from awesome_agent.application.composition import compose_local_application
from awesome_agent.application.contracts import StatusSnapshot, thread_display_id
from awesome_agent.conversation import ConversationService
from awesome_agent.core.events import CollectingEventSink
from awesome_agent.core.workspace import WorkspaceTrustService, resolve_workspace
from awesome_agent.modeling import ModelIdentitySnapshot
from awesome_agent.paths import AwesomePaths
from awesome_agent.storage.conversations import SQLiteConversationRepositories
from awesome_agent.storage.trust import SQLiteWorkspaceTrustStore
from awesome_agent.version import PRODUCT_VERSION


def test_status_snapshot_is_exact_and_resume_friendly() -> None:
    full_thread_id = "thread_3f8a1c2d111122223333444455556666"
    snapshot = StatusSnapshot(
        version=PRODUCT_VERSION,
        workspace_path="C:\\workspace",
        thread_title="Feature auth",
        thread_id=full_thread_id,
        thread_display_id=thread_display_id(full_thread_id),
        model_identity=ModelIdentitySnapshot.from_models(
            configured_model="deepseek/deepseek-v4-flash",
            effective_model="deepseek/deepseek-v4-flash",
        ),
        model_status="configured",
        thinking_enabled=False,
        skill_mode="auto",
        local_memory_enabled=False,
        mem0_enabled=False,
        mcp_ready=2,
        mcp_degraded=0,
        operation_status="idle",
        operation_id=None,
        configuration_valid=True,
        configuration_diagnostic_count=0,
    )

    assert snapshot.thread_display_id == "thread_3f8a1c2d"
    assert set(snapshot.model_dump(mode="json")) == {
        "version",
        "workspace_path",
        "thread_title",
        "thread_id",
        "thread_display_id",
        "model_identity",
        "model_status",
        "thinking_enabled",
        "skill_mode",
        "local_memory_enabled",
        "mem0_enabled",
        "mcp_ready",
        "mcp_degraded",
        "operation_status",
        "operation_id",
        "configuration_valid",
        "configuration_diagnostic_count",
        "permission_mode",
    }
    serialized = snapshot.model_dump_json()
    for excluded in ("trusted", "branch", "usage", "secret", "database", "dirty"):
        assert excluded not in serialized.casefold()

    colliding = "thread_3f8a1c2d999922223333444455556666"
    assert (
        thread_display_id(
            full_thread_id,
            candidate_ids=(full_thread_id, colliding),
        )
        == "thread_3f8a1c2d1"
    )


@pytest.mark.asyncio
async def test_status_command_returns_typed_snapshot_not_application_dump(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    paths = AwesomePaths.from_home(home)
    identity = resolve_workspace(workspace)
    WorkspaceTrustService(SQLiteWorkspaceTrustStore(paths.application_db)).accept(
        identity
    )
    conversation = ConversationService(
        store=SQLiteConversationRepositories(paths.application_db)
    )
    thread = conversation.create_thread(identity.key, "Feature auth")
    application = await compose_local_application(
        home=home,
        workspace=workspace,
        event_sink=CollectingEventSink(),
        environ={"DEEPSEEK_API_KEY": "fake-key"},
    )

    assert (await application.initialize()).ok is True
    resumed = await application.execute_command(
        CommandIntent(name=CommandName.RESUME, arguments=(thread.id,))
    )
    assert resumed.ok is True
    status = await application.execute_command(CommandIntent(name=CommandName.STATUS))
    assert status.ok is True
    assert status.value is not None
    assert status.value.status is CommandStatus.SUCCESS

    snapshot = StatusSnapshot.model_validate(status.value.data)
    application_state = await application.get_state()
    assert application_state.ok is True
    assert application_state.value is not None
    assert snapshot.version == PRODUCT_VERSION
    assert snapshot.workspace_path == str(workspace)
    assert snapshot.thread_title == "Feature auth"
    assert snapshot.thread_id == thread.id
    assert snapshot.thread_display_id == thread_display_id(thread.id)
    assert snapshot.model_identity == application_state.value.model_identity
    assert snapshot.model_identity.provider == "deepseek"
    assert snapshot.model_identity.effective_model == ("deepseek/deepseek-v4-flash")
    assert snapshot.model_identity.fallback_active is False
    assert snapshot.model_status == "configured"
    assert snapshot.operation_status == "idle"
    assert snapshot.local_memory_enabled is False
    assert snapshot.mem0_enabled is False
    assert snapshot.configuration_valid is True
    assert snapshot.configuration_diagnostic_count == 0
    assert "secret_status" not in status.value.data
    await application.shutdown()
