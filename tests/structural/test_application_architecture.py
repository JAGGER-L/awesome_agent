from pathlib import Path
from typing import Any, cast

from awesome_agent.application import composition as application_composition
from awesome_agent.application.command_results import ThreadTransitionSnapshot
from awesome_agent.application.commands import COMMAND_OWNERS, CommandOwner


def test_local_application_is_the_surface_facing_facade() -> None:
    application = Path("src/awesome_agent/application")

    assert "class LocalApplication:" in (application / "facade.py").read_text(
        encoding="utf-8"
    )


def test_local_application_uniquely_owns_bootstrap_state() -> None:
    application = Path("src/awesome_agent/application")
    owners = {
        path.name
        for path in application.glob("*.py")
        if "ApplicationBootstrap()" in path.read_text(encoding="utf-8")
    }
    facade = (application / "facade.py").read_text(encoding="utf-8")

    assert owners == {"facade.py"}
    assert "def bootstrap_rejection(" in facade
    assert "self._bootstrap.rejection(operation)" in facade


def test_dispatcher_inventory_is_complete_and_composition_only_wires_it() -> None:
    core_commands = {
        name for name, owner in COMMAND_OWNERS.items() if owner is not CommandOwner.INK
    }
    composition = Path("src/awesome_agent/application/composition.py").read_text(
        encoding="utf-8"
    )

    assert len(core_commands) == 24
    assert "return await runtime.command_dispatcher.dispatch(intent)" in composition
    assert "CommandResult(" not in composition
    assert "CommandInteractionResult(" not in composition
    assert "CommandError(" not in composition


def test_slash_commands_have_no_hidden_turn_submission_path() -> None:
    application = Path("src/awesome_agent/application")
    extension_commands = (application / "extension_commands.py").read_text(
        encoding="utf-8"
    )

    assert "submit_turn" not in extension_commands


def test_turn_coordinator_has_no_second_post_answer_finalizer() -> None:
    turns = Path("src/awesome_agent/application/turns.py").read_text(encoding="utf-8")

    assert "post_answer_memory" not in turns


def test_pending_input_is_not_a_core_or_storage_concept() -> None:
    roots = (
        Path("src/awesome_agent"),
        Path("protocol/fixtures"),
    )
    offenders = [
        path
        for root in roots
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix in {".py", ".json"}
        and "PendingInput" in path.read_text(encoding="utf-8")
    ]

    assert offenders == []


def test_application_owns_the_authoritative_thread_transition() -> None:
    application = Path("src/awesome_agent/application")
    conversation_commands = (application / "conversation_commands.py").read_text(
        encoding="utf-8"
    )
    composition = (application / "composition.py").read_text(encoding="utf-8")

    assert tuple(ThreadTransitionSnapshot.model_fields) == (
        "reason",
        "application",
        "thread",
    )
    assert "ThreadTransitionSnapshot(" in conversation_commands
    assert "application = await self._application_snapshot()" in conversation_commands
    assert "page = await self._thread_snapshot(" in conversation_commands
    assert "application=application" in conversation_commands
    assert "thread=page" in conversation_commands
    assert "application_snapshot=self.application_state" in composition
    assert "thread_snapshot=self.thread_state" in composition


def test_workspace_runtime_is_one_immutable_service_graph_snapshot() -> None:
    runtime = application_composition.WorkspaceRuntime
    composition = Path("src/awesome_agent/application/composition.py").read_text(
        encoding="utf-8"
    )

    assert cast(Any, runtime).__dataclass_params__.frozen is True
    assert tuple(runtime.__slots__) == (
        "sources",
        "application_config",
        "conversation",
        "turns",
        "commands",
        "thread_export",
        "command_dispatcher",
        "diagnostic_commands",
        "change_commands",
        "permission_commands",
        "web_commands",
        "provider_configuration",
        "direct",
        "extensions",
        "context",
        "tool_registry",
        "model_catalog",
        "local_memory",
        "mem0_session",
        "mcp",
        "change_scope",
        "change_store",
        "change_analyzer",
        "change_operations",
        "workspace_branch",
        "workspace_instruction_snapshot",
        "web_available",
        "web_diagnostic_code",
        "resources",
    )
    assert not hasattr(application_composition, "_ACTIVATION_STATE_FIELDS")
    assert "runtime = self._require_runtime()" in composition
    assert "candidate = await self._build_workspace_runtime" in composition
    assert "self._runtime = candidate" in composition
    assert "_snapshot_activation_state" not in composition
    assert "_restore_activation_state" not in composition
