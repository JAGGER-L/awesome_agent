from __future__ import annotations

import json
from pathlib import Path

from awesome_agent.application.commands import COMMAND_OWNERS, CommandName
from awesome_agent.contract_versions import PROTOCOL_VERSION

ROOT = Path(__file__).resolve().parents[3]


def test_command_authority_matches_protocol_fixture_exactly() -> None:
    fixture = json.loads(
        (ROOT / f"protocol/fixtures/v{PROTOCOL_VERSION}/commands.json").read_text(
            encoding="utf-8"
        )
    )
    expected = [
        {"name": name.value, "owner": COMMAND_OWNERS[name].value}
        for name in CommandName
    ]

    assert fixture == {"commands": expected}
    assert len(expected) == 30


def test_removed_command_names_are_not_accepted() -> None:
    removed = {
        "skill",
        "workplace",
        "review",
        "debug",
        "test",
        "commit",
        "editor",
        "clear",
        "exit",
    }

    assert removed.isdisjoint({name.value for name in CommandName})
