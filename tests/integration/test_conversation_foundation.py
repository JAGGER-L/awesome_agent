from __future__ import annotations

from pathlib import Path

from awesome_agent.config import (
    ThreadConfigState,
    load_config_sources,
    resolve_application_config,
    resolve_turn_config,
)
from awesome_agent.conversation import (
    ConversationService,
    ThreadEntryKind,
    UsageSummary,
)
from awesome_agent.paths import AwesomePaths
from awesome_agent.storage.conversations import SQLiteConversationRepositories


def test_fresh_home_multi_thread_history_survives_restart_without_checkpoint(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    paths = AwesomePaths.from_home(home)
    loaded = load_config_sources(
        paths=paths,
        workspace=workspace,
        workspace_trusted=True,
        environ={"DEEPSEEK_API_KEY": "test-secret"},
    )
    application = resolve_application_config(loaded)
    turn_config = resolve_turn_config(
        application,
        thread=ThreadConfigState(),
        environ={},
    )
    service = ConversationService(
        store=SQLiteConversationRepositories(paths.application_db)
    )
    first = service.create_thread("workspace_key", "First")
    second = service.create_thread("workspace_key", "Second")
    turn = service.begin_turn(first.id, "Inspect", turn_config)
    service.complete_turn(
        turn.id,
        "Inspection complete",
        UsageSummary(input_tokens=20, output_tokens=5, model_calls=1),
        "completed",
    )
    service.append_direct_command(
        first.id,
        "$ pytest\nexit=0",
        {"exit_code": 0},
    )
    service.begin_turn(second.id, "Independent", turn_config)

    reopened = ConversationService(
        store=SQLiteConversationRepositories(paths.application_db)
    )
    first_view = reopened.read_thread(first.id)
    second_view = reopened.read_thread(second.id)

    assert [entry.kind for entry in first_view.entries] == [
        ThreadEntryKind.USER_MESSAGE,
        ThreadEntryKind.ASSISTANT_MESSAGE,
        ThreadEntryKind.DIRECT_COMMAND,
    ]
    assert first_view.turns[0].status.value == "completed"
    assert second_view.turns[0].status.value == "in_progress"
    assert paths.application_db.is_file()
    assert not paths.checkpoint_db.exists()
    assert "test-secret" not in paths.application_db.read_bytes().decode(
        "utf-8",
        errors="ignore",
    )
