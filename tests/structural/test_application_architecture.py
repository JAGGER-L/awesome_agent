from pathlib import Path

from awesome_agent.application.command_results import ThreadTransitionSnapshot
from awesome_agent.application.commands import COMMAND_OWNERS, CommandOwner


def test_local_application_is_the_surface_facing_facade() -> None:
    application = Path("src/awesome_agent/application")

    assert "class LocalApplication:" in (application / "facade.py").read_text(
        encoding="utf-8"
    )


def test_dispatcher_inventory_is_complete_and_composition_only_wires_it() -> None:
    core_commands = {
        name for name, owner in COMMAND_OWNERS.items() if owner is not CommandOwner.INK
    }
    composition = Path("src/awesome_agent/application/composition.py").read_text(
        encoding="utf-8"
    )

    assert len(core_commands) == 20
    assert "return await self._command_dispatcher.dispatch(intent)" in composition
    assert "CommandResult(" not in composition
    assert "CommandInteractionResult(" not in composition
    assert "CommandError(" not in composition


def test_slash_commands_have_no_hidden_turn_submission_path() -> None:
    application = Path("src/awesome_agent/application")
    extension_commands = (application / "extension_commands.py").read_text(
        encoding="utf-8"
    )
    bundled = Path("src/awesome_agent/extensions/skills/bundled")

    assert "submit_turn" not in extension_commands
    assert "async def init" not in extension_commands
    assert not (bundled / "init").exists()


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
