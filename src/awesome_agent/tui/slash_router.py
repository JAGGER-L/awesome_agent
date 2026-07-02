from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from awesome_agent.cli.slash_commands import (
    SlashCommand,
    SlashCommandKind,
    slash_command_help,
)
from awesome_agent.surfaces.client import SurfaceThread
from awesome_agent.tui.chat_state import ChatEventKind, ChatMessage, ChatSessionState


class ChatSemanticClient(Protocol):
    def create_thread(self, title: str) -> SurfaceThread | dict[str, object]: ...

    def runtime_status(self) -> dict[str, object]: ...

    def list_models(self) -> list[dict[str, object]]: ...

    def memory_summary(self) -> dict[str, object]: ...

    def list_threads(self) -> list[SurfaceThread | dict[str, object]]: ...

    def list_skills(self) -> list[dict[str, object]]: ...

    def list_tools(self) -> dict[str, list[dict[str, object]]]: ...

    def mcp_status(self) -> list[dict[str, object]]: ...

    def list_uploads(self, thread_id: str | None) -> list[dict[str, object]]: ...

    def list_current_artifacts(
        self,
        thread_id: str | None,
        run_id: str | None,
    ) -> list[dict[str, object]]: ...

    def usage_summary(
        self,
        thread_id: str | None,
        run_id: str | None,
    ) -> dict[str, object]: ...

    def config_summary(self) -> dict[str, object]: ...


class SlashRouter:
    def __init__(self, client: ChatSemanticClient) -> None:
        self.client = client

    def handle(
        self,
        command: SlashCommand,
        state: ChatSessionState,
    ) -> ChatMessage:
        if command.kind is SlashCommandKind.HELP:
            return ChatMessage.system(slash_command_help())
        if command.kind is SlashCommandKind.THREADS:
            threads = self.client.list_threads()
            return ChatMessage.system(
                format_thread_list(thread_summaries(threads, state.backend_thread_id))
            )
        if command.kind is SlashCommandKind.RESUME:
            target = command.argument or "<thread id or title>"
            return ChatMessage.system(
                f"Thread resume for {target} is not backed by the local API yet."
            )
        if command.kind is SlashCommandKind.STATUS:
            status = self.client.runtime_status()
            context = state.launch_context
            if context is not None:
                status = {
                    **status,
                    context.context_kind: context.display_path,
                    "thread": str(state.thread_id),
                    "run": state.current_run_id or "-",
                }
            return ChatMessage.system(
                " ".join(f"{key}={value}" for key, value in status.items()),
                kind=ChatEventKind.RUN,
            )
        if command.kind is SlashCommandKind.MODELS:
            if state.first_run_summary is not None:
                summary = state.first_run_summary
                return ChatMessage.system(
                    _format_models(
                        [
                            {
                                "role": "default",
                                "name": summary.model_name,
                                "provider": "deepseek",
                                "configured": summary.model_api_key_configured,
                                "api_key_env": summary.model_api_key_env,
                                "api_key_present": summary.model_api_key_configured,
                                "source": "first_run_summary",
                            }
                        ],
                        state,
                    )
                )
            models = self.client.list_models()
            return ChatMessage.system(_format_models(models, state))
        if command.kind is SlashCommandKind.MEMORY:
            memory = self.client.memory_summary()
            return ChatMessage.system(
                " ".join(f"{key}={value}" for key, value in memory.items())
            )
        if command.kind is SlashCommandKind.SKILLS:
            skills = self.client.list_skills()
            if not skills:
                return ChatMessage.system("No skills reported by the local API.")
            return ChatMessage.system(
                "\n".join(
                    _label(
                        item,
                        "id",
                        suffix_keys=("version", "source_id", "risk_level"),
                    )
                    for item in skills
                )
            )
        if command.kind is SlashCommandKind.TOOLS:
            groups = self.client.list_tools()
            lines = [_format_tool_group(name, items) for name, items in groups.items()]
            return ChatMessage.system("\n".join(lines))
        if command.kind is SlashCommandKind.MCP:
            servers = self.client.mcp_status()
            if not servers:
                return ChatMessage.system("No MCP servers reported.")
            return ChatMessage.system(
                "\n".join(
                    _label(item, "id", suffix_keys=("status", "type", "trust"))
                    for item in servers
                )
            )
        if command.kind is SlashCommandKind.UPLOADS:
            uploads = self.client.list_uploads(state.backend_thread_id)
            if not uploads:
                return ChatMessage.system("No uploads for this thread.")
            return ChatMessage.system(
                "\n".join(
                    str(item.get("path") or item.get("name") or item)
                    for item in uploads
                )
            )
        if command.kind is SlashCommandKind.ARTIFACTS:
            artifacts = self.client.list_current_artifacts(
                state.backend_thread_id,
                state.current_run_id,
            )
            if not artifacts:
                return ChatMessage.system("No artifacts for the current run.")
            return ChatMessage.system(
                "\n".join(
                    str(item.get("path") or item.get("name") or item)
                    for item in artifacts
                )
            )
        if command.kind is SlashCommandKind.DETAILS:
            return ChatMessage.system(
                "Verbose activity rendering toggled. Use /details again to switch back."
            )
        if command.kind is SlashCommandKind.USAGE:
            usage = self.client.usage_summary(
                state.backend_thread_id,
                state.current_run_id,
            )
            return ChatMessage.system(
                " ".join(f"{key}={value}" for key, value in usage.items())
            )
        if command.kind is SlashCommandKind.CONFIG:
            if state.first_run_summary is not None:
                summary = state.first_run_summary
                key_status = "set" if summary.model_api_key_configured else "missing"
                return ChatMessage.system(
                    "\n".join(
                        [
                            f"home={summary.home}",
                            (
                                f"user_config={summary.user_config} "
                                f"exists={summary.user_config_exists}"
                            ),
                            (
                                f"project_config={summary.project_config} "
                                f"exists={summary.project_config_exists}"
                            ),
                            (
                                f"project_env={summary.project_env} "
                                f"exists={summary.project_env_exists}"
                            ),
                            f"{summary.model_api_key_env}={key_status}",
                        ]
                    )
                )
            config = self.client.config_summary()
            return ChatMessage.system(
                " ".join(f"{key}={value}" for key, value in config.items())
            )
        if command.kind is SlashCommandKind.NEW:
            target = command.argument or "New conversation"
            thread = self.client.create_thread(target)
            return ChatMessage.system(
                f"New conversation started: {_thread_label(thread)}",
                kind=ChatEventKind.RUN,
            )
        return ChatMessage.system(
            f"Unknown command. Try /help. Current thread={state.thread_id}",
            kind=ChatEventKind.ERROR,
        )


@dataclass(frozen=True, slots=True)
class ThreadSummary:
    id: str
    short_id: str
    title: str
    current: bool = False
    context_label: str | None = None
    updated_label: str | None = None


def thread_summaries(
    threads: list[SurfaceThread | dict[str, object]],
    current_thread_id: str | None,
) -> list[ThreadSummary]:
    return [
        _thread_summary(thread, current_thread_id=current_thread_id)
        for thread in threads
    ]


def format_thread_list(threads: list[ThreadSummary]) -> str:
    if not threads:
        return "No conversations yet."
    lines = ["Threads"]
    for thread in threads:
        marker = "*" if thread.current else " "
        updated = thread.updated_label or "-"
        context = _public_context_label(thread.context_label)
        lines.append(
            f"{marker} {thread.title}  {thread.short_id}  {updated}  {context}"
        )
    return "\n".join(lines)


def _format_tool_group(name: str, items: list[dict[str, object]]) -> str:
    if not items:
        return f"{name}: -"
    rendered = [
        _label(item, "name", suffix_keys=("risk_level", "health")) for item in items
    ]
    return f"{name}: {', '.join(rendered)}"


def _format_models(
    models: list[dict[str, object]],
    state: ChatSessionState,
) -> str:
    if not models:
        return "No models configured.\nlast turn: none yet"
    lines = ["Models"]
    for item in models:
        configured = "yes" if item.get("configured") is True else "no"
        provider = item.get("provider") or "unknown"
        role = item.get("role") or "model"
        name = item.get("name") or "-"
        line = f"{role}: {name}  provider={provider}  configured={configured}"
        api_key_env = item.get("api_key_env")
        api_key_present = item.get("api_key_present")
        if api_key_env is not None:
            present = "yes" if api_key_present is True else "no"
            line = f"{line}  api_key_env={api_key_env} present={present}"
            if api_key_present is not True:
                line = f"{line} (missing {api_key_env})"
        lines.append(line)
        base_url = item.get("base_url")
        if base_url:
            lines.append(f"base_url: {base_url}")
    if state.last_requested_model is None:
        lines.append("last turn: none yet")
    else:
        parts = [
            f"requested={state.last_requested_model}",
            f"response={state.last_response_model or '-'}",
            f"provider={state.last_model_provider or '-'}",
        ]
        if state.last_model_response_id:
            parts.append(f"response_id={state.last_model_response_id}")
        lines.append(f"last turn: {' '.join(parts)}")
    lines.append("note: model self-description is not authoritative.")
    return "\n".join(lines)


def _label(
    item: dict[str, object],
    key: str,
    *,
    suffix_keys: tuple[str, ...],
) -> str:
    label = str(item.get(key) or item.get("name") or item.get("id") or item)
    suffix = [
        f"{suffix_key}={item[suffix_key]}"
        for suffix_key in suffix_keys
        if item.get(suffix_key) not in (None, "", [])
    ]
    if not suffix:
        return label
    return f"{label} ({', '.join(suffix)})"


def _thread_label(thread: SurfaceThread | dict[str, object]) -> str:
    if isinstance(thread, SurfaceThread):
        context = f" {thread.context_label}" if thread.context_label else ""
        return f"{thread.title} {thread.short_id}{context}".strip()
    thread_id = str(thread.get("id") or "-")
    title = str(thread.get("title") or thread_id)
    return f"{title} {thread_id[:8]}".strip()


def _thread_summary(
    thread: SurfaceThread | dict[str, object],
    *,
    current_thread_id: str | None,
) -> ThreadSummary:
    if isinstance(thread, SurfaceThread):
        return ThreadSummary(
            id=thread.id,
            short_id=thread.short_id,
            title=thread.title,
            current=thread.id == current_thread_id,
            context_label=thread.context_label,
            updated_label=thread.updated_label,
        )
    thread_id = str(thread.get("id") or "-")
    title = str(thread.get("title") or thread_id)
    context = thread.get("context_path") or thread.get("context_label")
    updated = thread.get("updated_label")
    return ThreadSummary(
        id=thread_id,
        short_id=thread_id[:8],
        title=title,
        current=thread_id == current_thread_id,
        context_label=str(context) if context is not None else None,
        updated_label=str(updated) if updated is not None else None,
    )


def _public_context_label(context_label: str | None) -> str:
    if not context_label:
        return "-"
    normalized = context_label.replace("\\", "/")
    if normalized.startswith("/mnt/user-data/"):
        return "workspace"
    return context_label
