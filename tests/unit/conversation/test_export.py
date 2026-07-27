from __future__ import annotations

import json
from datetime import UTC, datetime

from awesome_agent.conversation import (
    AssistantEntryMetadata,
    Thread,
    ThreadEntry,
    ThreadEntryKind,
    ThreadLineage,
    ThreadView,
    render_thread_export,
)
from awesome_agent.core.citations import Citation


def _view() -> ThreadView:
    timestamp = datetime(2026, 7, 27, 9, 30, tzinfo=UTC)
    thread = Thread(
        id="thread_export",
        workspace_key="workspace_private",
        title="Exported Thread",
        current_model="deepseek/deepseek-v4-flash",
        created_at=timestamp,
        updated_at=timestamp,
    )
    citations = AssistantEntryMetadata(
        citations=(
            Citation(
                id="S1",
                title="Primary source",
                url="https://example.com/source",
            ),
        )
    ).model_dump(mode="json")
    return ThreadView(
        thread=thread,
        entries=(
            ThreadEntry(
                id="entry_user",
                thread_id=thread.id,
                sequence=1,
                kind=ThreadEntryKind.USER_MESSAGE,
                content="Question",
                client_message_id="client_export",
                created_at=timestamp,
            ),
            ThreadEntry(
                id="entry_assistant",
                thread_id=thread.id,
                sequence=2,
                kind=ThreadEntryKind.ASSISTANT_MESSAGE,
                content="Answer [[S1]]",
                metadata=citations,
                created_at=timestamp,
            ),
            ThreadEntry(
                id="entry_uncited",
                thread_id=thread.id,
                sequence=3,
                kind=ThreadEntryKind.ASSISTANT_MESSAGE,
                content="Answer without sources",
                created_at=timestamp,
            ),
            ThreadEntry(
                id="entry_direct",
                thread_id=thread.id,
                sequence=4,
                kind=ThreadEntryKind.DIRECT_COMMAND,
                content="git status",
                metadata={"operation_id": "operation_private"},
                created_at=timestamp,
            ),
        ),
    )


def test_json_thread_export_is_versioned_deterministic_and_entry_scoped() -> None:
    source = _view()
    view = source.model_copy(update={"entries": tuple(reversed(source.entries))})

    rendered = render_thread_export(view, format="json")
    payload = json.loads(rendered)

    assert rendered == render_thread_export(view, format="json")
    assert rendered.endswith("\n")
    assert payload["schema"] == "awesome.thread-export"
    assert payload["version"] == 1
    assert "workspace_key" not in payload["thread"]
    assert payload["thread"]["lineage"] is None
    assert [entry["sequence"] for entry in payload["entries"]] == [1, 2, 3, 4]
    assert "citations" not in payload["entries"][0]
    assert payload["entries"][1]["citations"] == [
        {
            "id": "S1",
            "title": "Primary source",
            "url": "https://example.com/source",
        }
    ]
    assert payload["entries"][2]["citations"] == []
    assert "metadata" not in payload["entries"][3]


def test_markdown_thread_export_emits_sources_only_for_cited_assistant() -> None:
    source = _view()
    view = source.model_copy(update={"entries": tuple(reversed(source.entries))})

    rendered = render_thread_export(view, format="markdown")

    assert rendered == render_thread_export(view, format="markdown")
    assert rendered.startswith("# Exported Thread\n")
    assert "<!-- awesome-thread-export:v1 -->" in rendered
    assert "- Lineage: none" in rendered
    assert rendered.count("#### Sources") == 1
    assert "[[S1]] Primary source — https://example.com/source" in rendered
    assert rendered.index("### User · 1") < rendered.index("### Assistant · 2")
    assert rendered.index("### Assistant · 2") < rendered.index("### Assistant · 3")
    assert rendered.index("### Assistant · 3") < rendered.index(
        "### Direct command · 4"
    )


def test_thread_export_preserves_materialized_lineage() -> None:
    source = _view()
    lineage = ThreadLineage(
        kind="fork",
        source_thread_id="thread_source",
        source_turn_id="turn_source",
    )
    view = source.model_copy(
        update={"thread": source.thread.model_copy(update={"lineage": lineage})}
    )

    payload = json.loads(render_thread_export(view, format="json"))
    markdown = render_thread_export(view, format="markdown")

    assert payload["thread"]["lineage"] == {
        "kind": "fork",
        "source_thread_id": "thread_source",
        "source_turn_id": "turn_source",
    }
    assert (
        "- Lineage: `fork` from Thread `thread_source`, Turn `turn_source`" in markdown
    )


def test_thread_dump_keeps_required_nullable_lineage_with_exclude_none() -> None:
    thread = _view().thread

    assert thread.model_dump(mode="json", exclude_none=True)["lineage"] is None
    assert "lineage" not in thread.model_dump(
        mode="json",
        exclude_none=True,
        exclude={"lineage"},
    )
    assert thread.model_dump(mode="json", include={"id"}) == {"id": thread.id}
