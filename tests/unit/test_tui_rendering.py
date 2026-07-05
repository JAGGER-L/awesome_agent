from __future__ import annotations

from rich.text import Text

from awesome_agent.surfaces.client import ChangedFileSummary
from awesome_agent.tui.chat_state import (
    ChatEventKind,
    ChatMessage,
    ThoughtBlock,
    ToolGroup,
)
from awesome_agent.tui.events import (
    ApprovalPromptState,
    TeamDisplayEvent,
    ToolDisplayEvent,
    ToolTimelineEntry,
)
from awesome_agent.tui.rendering import (
    render_approval_prompt,
    render_changed_files,
    render_message,
    render_team_event,
    render_thought,
    render_tool_event,
    render_transcript,
)


def test_user_message_uses_prompt_marker() -> None:
    rendered = render_message(ChatMessage.user("What can you do?"))

    assert rendered.plain.startswith("> What can you do?")


def test_command_message_uses_command_marker() -> None:
    rendered = render_message(ChatMessage.command("/status"))

    assert rendered.plain.startswith("> /status")
    assert rendered.spans


def test_assistant_message_uses_answer_marker() -> None:
    rendered = render_message(ChatMessage.assistant("I can help.")).plain

    assert "assistant" in rendered
    assert "I can help." in rendered
    assert not rendered.startswith("[message]")


def test_assistant_message_renders_collapsed_changed_files() -> None:
    message = ChatMessage.assistant(
        "Done.",
        changed_files=(
            ChangedFileSummary(path="a.py", status="created"),
            ChangedFileSummary(path="b.py", status="updated"),
            ChangedFileSummary(path="c.py", status="deleted"),
            ChangedFileSummary(path="d.py", status="updated"),
        ),
    )

    rendered = render_message(message, changed_files_expanded=False).plain

    assert "assistant\nDone." in rendered
    assert "Changed files" in rendered
    assert "created a.py" in rendered
    assert "updated b.py" in rendered
    assert "deleted c.py" in rendered
    assert "d.py" not in rendered
    assert "1 more file (ctrl+e to expand)" in rendered


def test_assistant_message_renders_expanded_changed_files() -> None:
    message = ChatMessage.assistant(
        "Done.",
        changed_files=(
            ChangedFileSummary(path="a.py", status="created"),
            ChangedFileSummary(path="b.py", status="updated"),
            ChangedFileSummary(path="c.py", status="deleted"),
            ChangedFileSummary(path="d.py", status="updated"),
        ),
    )

    rendered = render_message(message, changed_files_expanded=True).plain

    assert "updated d.py" in rendered
    assert "ctrl+e to collapse" in rendered


def test_assistant_changed_files_bounds_long_paths_to_one_line() -> None:
    message = ChatMessage.assistant(
        "Done.",
        changed_files=(
            ChangedFileSummary(path=f"src/{'nested/' * 30}file.py", status="updated"),
        ),
    )

    rendered = render_message(message, changed_files_expanded=False).plain

    changed_line = next(line for line in rendered.splitlines() if "updated" in line)
    assert len(changed_line) <= 120
    assert "\n" not in changed_line


def test_error_message_is_actionable() -> None:
    rendered = render_message(ChatMessage.error("Provider timed out")).plain

    assert "error" in rendered.lower()
    assert "Provider timed out" in rendered


def test_normal_messages_do_not_expose_internal_kind_prefixes() -> None:
    messages = [
        ChatMessage.user("hi"),
        ChatMessage.assistant("hello"),
        ChatMessage.system("ready"),
        ChatMessage.system("Run started", kind=ChatEventKind.RUN),
    ]

    rendered = [render_message(message).plain for message in messages]

    assert not any(
        item.startswith(("[message]", "[model]", "[you]")) for item in rendered
    )


def test_transcript_separates_messages_with_blank_lines() -> None:
    transcript = render_transcript(
        [ChatMessage.user("hi"), ChatMessage.assistant("hello")]
    ).plain

    assert transcript == "> hi\n\nassistant\nhello"


def test_collapsed_thought_hides_reasoning_text() -> None:
    rendered = render_thought(
        ThoughtBlock(
            text="private reasoning",
            active=False,
            collapsed=True,
            elapsed_seconds=1,
        )
    ).plain

    assert rendered == "Thought for 1s (ctrl+o to expand)"
    assert "private reasoning" not in rendered


def test_expanded_thought_shows_reasoning_text() -> None:
    rendered = render_thought(
        ThoughtBlock(
            text="bounded reasoning",
            active=False,
            collapsed=False,
            elapsed_seconds=1,
        )
    ).plain

    assert "ctrl+o to collapse" in rendered
    assert "bounded reasoning" in rendered


def test_transcript_renders_thought_under_owning_turn() -> None:
    transcript = render_transcript(
        [
            ChatMessage.user("first", turn_id="turn-1"),
            ChatMessage.assistant("one", turn_id="turn-1"),
            ChatMessage.user("second", turn_id="turn-2"),
            ChatMessage.assistant("two", turn_id="turn-2"),
        ],
        thought_blocks={
            "turn-1": ThoughtBlock(
                text="first thought",
                active=False,
                collapsed=False,
                elapsed_seconds=1,
            ),
            "turn-2": ThoughtBlock(
                text="second thought",
                active=False,
                collapsed=False,
                elapsed_seconds=2,
            ),
        },
    ).plain

    assert transcript.index("first thought") < transcript.index("one")
    assert transcript.index("second thought") > transcript.index("> second")
    assert transcript.index("second thought") < transcript.index("two")


def test_later_thought_does_not_overwrite_previous_turn() -> None:
    transcript = render_transcript(
        [
            ChatMessage.user("first", turn_id="turn-1"),
            ChatMessage.assistant("one", turn_id="turn-1"),
            ChatMessage.user("second", turn_id="turn-2"),
        ],
        thought_blocks={
            "turn-1": ThoughtBlock(
                text="first thought",
                active=False,
                collapsed=False,
                elapsed_seconds=1,
            ),
            "turn-2": ThoughtBlock(
                text="second thought",
                active=False,
                collapsed=True,
                elapsed_seconds=2,
            ),
        },
    ).plain

    assert "first thought" in transcript
    assert "second thought" not in transcript
    assert "Thought for 2s (ctrl+o to expand)" in transcript


def test_tool_call_renders_collapsed_by_default() -> None:
    event = ToolDisplayEvent(
        name="write_file",
        summary="created snake-game.html",
        details={"path": "snake-game.html", "stdout": "raw output"},
    )

    rendered = render_tool_event(event, details_enabled=False).plain

    assert "Tool - write_file" in rendered
    assert "created snake-game.html" in rendered
    assert "path:" not in rendered
    assert "stdout:" not in rendered


def test_completed_tool_call_uses_green_label() -> None:
    event = ToolDisplayEvent(
        name="repo.status",
        summary="completed",
    )

    rendered = render_tool_event(event, details_enabled=False)

    assert rendered.plain.startswith("Tool - repo.status")
    assert rendered.spans[0].style == "green"


def test_failed_tool_call_uses_red_label() -> None:
    event = ToolDisplayEvent(
        name="repo.status",
        summary="failed",
    )

    rendered = render_tool_event(event, details_enabled=False)

    assert rendered.plain.startswith("Tool - repo.status")
    assert rendered.spans[0].style == "red"


def test_tool_message_success_label_is_green() -> None:
    message = ChatMessage.system(
        "Tool - repo.status\ncompleted",
        kind=ChatEventKind.TOOL,
    )

    rendered = render_message(message)

    styles = _styles_for_text(rendered, "tool")
    assert "green" in styles
    assert "red" not in styles
    assert "magenta" not in styles


def test_tool_message_failure_label_is_red() -> None:
    message = ChatMessage.system(
        "Tool - repo.status\nfailed: boom",
        kind=ChatEventKind.TOOL,
    )

    rendered = render_message(message)

    assert "red" in _styles_for_text(rendered, "tool")


def test_tool_call_renders_neutral_collapsed_timeline_summary() -> None:
    message = ChatMessage.system(
        "",
        kind=ChatEventKind.TOOL,
        tool_group=ToolGroup(
            entries=(
                ToolTimelineEntry(name="repo.read", summary="completed"),
                ToolTimelineEntry(name="shell.execute", summary="failed"),
            )
        ),
    )

    rendered = render_message(message, tool_groups_expanded=False).plain

    assert "tools: called 2" in rendered
    assert "1 completed" in rendered
    assert "1 failed" in rendered
    assert "ctrl+i to expand" in rendered
    assert "tool:" not in rendered


def test_tool_call_expanded_timeline_shows_failure_details() -> None:
    message = ChatMessage.system(
        "",
        kind=ChatEventKind.TOOL,
        tool_group=ToolGroup(
            entries=(
                ToolTimelineEntry(name="repo.read", summary="completed"),
                ToolTimelineEntry(
                    name="shell.execute",
                    summary="failed",
                    details={"error": "approval denied"},
                ),
            )
        ),
    )

    rendered = render_message(message, tool_groups_expanded=True).plain

    assert "tools: called 2" in rendered
    assert "repo.read - completed" in rendered
    assert "shell.execute - failed" in rendered
    assert "error: approval denied" in rendered
    assert "ctrl+i to collapse" in rendered


def test_tool_call_details_are_bounded_and_redacted() -> None:
    event = ToolDisplayEvent(
        name="run_command",
        summary="completed",
        details={
            "command": "python -m pytest",
            "stdout": f"OPENAI_API_KEY=sk-proj-abcdefghijklmnopqrstuvwxyz {'x' * 900}",
        },
    )

    rendered = render_tool_event(event, details_enabled=True).plain

    assert "command: python -m pytest" in rendered
    assert "stdout: OPENAI_API_KEY=[REDACTED:api_key]" in rendered
    assert len(rendered) < 900
    assert "..." in rendered
    assert "sk-proj-" not in rendered


def test_team_event_hides_details_until_details_mode() -> None:
    event = TeamDisplayEvent(
        title="Team",
        summary="Leader created 2 teammates",
        details={"message": "leader -> frontend-engineer"},
    )

    collapsed = render_team_event(event, details_enabled=False).plain
    expanded = render_team_event(event, details_enabled=True).plain

    assert "Leader created 2 teammates" in collapsed
    assert "leader -> frontend-engineer" not in collapsed
    assert "leader -> frontend-engineer" in expanded


def test_approval_prompt_renders_choices() -> None:
    prompt = ApprovalPromptState(
        run_id="run-1",
        approval_id="approval-1",
        title="Leader wants to create:",
        subject="snake-game.html",
    )

    rendered = render_approval_prompt(prompt).plain

    assert "Leader wants to create:" in rendered
    assert "snake-game.html" in rendered
    assert "> 1. approve once" in rendered
    assert "2. deny" in rendered
    assert "3. cancel run" in rendered


def test_changed_files_render_workspace_relative_paths() -> None:
    rendered = render_changed_files(
        [
            ChangedFileSummary(
                path="/mnt/user-data/workspace/snake-game.html",
                status="created",
                display_path="snake-game.html",
            ),
            ChangedFileSummary(path="README.md", status="updated"),
        ]
    ).plain

    assert "Changed files" in rendered
    assert "created snake-game.html" in rendered
    assert "updated README.md" in rendered
    assert "/mnt/user-data/workspace" not in rendered


def test_changed_files_empty_state_is_explicit() -> None:
    rendered = render_changed_files([]).plain

    assert rendered == "Changed files\n  none"


def _styles_for_text(text: Text, needle: str) -> set[str]:
    plain = text.plain
    styles: set[str] = set()
    start = plain.lower().index(needle.lower())
    end = start + len(needle)
    for span in text.spans:
        if span.start <= start and span.end >= end:
            styles.add(str(span.style))
    return styles
