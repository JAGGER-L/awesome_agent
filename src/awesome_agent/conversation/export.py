from __future__ import annotations

import json
from typing import Literal

from awesome_agent.contract_versions import (
    THREAD_EXPORT_JSON_SCHEMA,
    THREAD_EXPORT_VERSION,
)
from awesome_agent.conversation.models import (
    AssistantEntryMetadata,
    ThreadEntry,
    ThreadEntryKind,
    ThreadLineage,
    ThreadView,
)
from awesome_agent.core.citations import Citation

type ThreadExportFormat = Literal["markdown", "json"]


def render_thread_export(view: ThreadView, *, format: ThreadExportFormat) -> str:
    if format == "json":
        return _render_json(view)
    if format == "markdown":
        return _render_markdown(view)
    raise ValueError("Thread export format must be markdown or json.")


def _render_json(view: ThreadView) -> str:
    thread = view.thread
    payload = {
        "entries": [_json_entry(entry) for entry in _ordered_entries(view)],
        "schema": THREAD_EXPORT_JSON_SCHEMA,
        "thread": {
            "created_at": thread.created_at.isoformat(),
            "current_model": thread.current_model,
            "id": thread.id,
            "lineage": (
                None
                if thread.lineage is None
                else thread.lineage.model_dump(mode="json")
            ),
            "skill_mode": thread.skill_mode,
            "thinking_enabled": thread.thinking_enabled,
            "title": thread.title,
            "title_source": thread.title_source.value,
            "updated_at": thread.updated_at.isoformat(),
        },
        "version": THREAD_EXPORT_VERSION,
    }
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def _json_entry(entry: ThreadEntry) -> dict[str, object]:
    payload: dict[str, object] = {
        "content": entry.content,
        "created_at": entry.created_at.isoformat(),
        "id": entry.id,
        "kind": entry.kind.value,
        "sequence": entry.sequence,
    }
    if entry.kind is ThreadEntryKind.ASSISTANT_MESSAGE:
        payload["citations"] = [
            citation.model_dump(mode="json") for citation in _citations(entry)
        ]
    return payload


def _render_markdown(view: ThreadView) -> str:
    thread = view.thread
    lines = [
        f"# {_markdown_inline(thread.title)}",
        "",
        f"<!-- awesome-thread-export:v{THREAD_EXPORT_VERSION} -->",
        "",
        f"- Thread ID: `{_markdown_code(thread.id)}`",
        f"- Created: `{thread.created_at.isoformat()}`",
        f"- Updated: `{thread.updated_at.isoformat()}`",
        f"- Model: `{_markdown_code(thread.current_model or 'not selected')}`",
        f"- Thinking: `{'on' if thread.thinking_enabled else 'off'}`",
        f"- Skill mode: `{_markdown_code(thread.skill_mode)}`",
        _markdown_lineage(thread.lineage),
        "",
        "## Transcript",
        "",
    ]
    for entry in _ordered_entries(view):
        lines.extend(_markdown_entry(entry))
    return "\n".join(lines).rstrip() + "\n"


def _markdown_entry(entry: ThreadEntry) -> list[str]:
    labels = {
        ThreadEntryKind.USER_MESSAGE: "User",
        ThreadEntryKind.ASSISTANT_MESSAGE: "Assistant",
        ThreadEntryKind.DIRECT_COMMAND: "Direct command",
    }
    lines = [
        f"### {labels[entry.kind]} · {entry.sequence}",
        "",
        entry.content,
        "",
    ]
    citations = _citations(entry)
    if citations:
        lines.extend(("#### Sources", ""))
        lines.extend(_markdown_citation(citation) for citation in citations)
        lines.append("")
    return lines


def _citations(entry: ThreadEntry) -> tuple[Citation, ...]:
    if entry.kind is not ThreadEntryKind.ASSISTANT_MESSAGE:
        return ()
    return AssistantEntryMetadata.model_validate(entry.metadata).citations


def _ordered_entries(view: ThreadView) -> tuple[ThreadEntry, ...]:
    return tuple(sorted(view.entries, key=lambda entry: (entry.sequence, entry.id)))


def _markdown_citation(citation: Citation) -> str:
    return f"- [[{citation.id}]] {_markdown_inline(citation.title)} — {citation.url}"


def _markdown_lineage(lineage: ThreadLineage | None) -> str:
    if lineage is None:
        return "- Lineage: none"
    return (
        f"- Lineage: `{lineage.kind}` from Thread "
        f"`{_markdown_code(lineage.source_thread_id)}`, Turn "
        f"`{_markdown_code(lineage.source_turn_id)}`"
    )


def _markdown_inline(value: str) -> str:
    single_line = " ".join(value.splitlines())
    return single_line.replace("\\", "\\\\").replace("`", "\\`")


def _markdown_code(value: str) -> str:
    return value.replace("`", "\\`")


__all__ = ["ThreadExportFormat", "render_thread_export"]
