from pathlib import Path

from awesome_agent.application.commands import COMMAND_OWNERS, CommandOwner


def test_local_application_is_the_only_surface_facing_application_host() -> None:
    application = Path("src/awesome_agent/application")

    assert not (application / "headless.py").exists()
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

    assert len(core_commands) == 21
    assert "return await self._command_dispatcher.dispatch(intent)" in composition
    assert "CommandResult(" not in composition
    assert "CommandInteractionResult(" not in composition
    assert "CommandError(" not in composition
