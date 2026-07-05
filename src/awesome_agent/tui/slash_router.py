from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, cast

from awesome_agent.cli.slash_commands import (
    SlashCommand,
    SlashCommandKind,
    slash_command_help,
)
from awesome_agent.surfaces.client import ChangedFileSummary, SurfaceThread
from awesome_agent.surfaces.guidance import missing_api_key_guidance
from awesome_agent.tui.chat_state import ChatEventKind, ChatMessage, ChatSessionState


class ChatSemanticClient(Protocol):
    def create_thread(self, title: str) -> SurfaceThread | dict[str, object]: ...

    def list_models(self) -> dict[str, object]: ...

    def memory_summary(self) -> dict[str, object]: ...

    def list_threads(self) -> Sequence[SurfaceThread | dict[str, object]]: ...

    def list_skills(self) -> list[dict[str, object]]: ...

    def list_tools(self) -> dict[str, list[dict[str, object]]]: ...

    def mcp_status(self) -> list[dict[str, object]]: ...

    def usage_summary(
        self,
        thread_id: str | None,
        run_id: str | None,
    ) -> dict[str, object]: ...

    def config_summary(self, thread_id: str | None = None) -> dict[str, object]: ...


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
        if command.kind is SlashCommandKind.MODEL:
            if state.first_run_summary is not None:
                summary = state.first_run_summary
                return ChatMessage.system(
                    _format_models(
                        {
                            "providers": [
                                {
                                    "id": "deepseek",
                                    "display_name": "DeepSeek",
                                    "configured": summary.model_api_key_configured,
                                    "credential_env": summary.model_api_key_env,
                                    "api_key_present": (
                                        summary.model_api_key_configured
                                    ),
                                    "models": [
                                        {
                                            "id": summary.model_name,
                                            "display_name": summary.model_name,
                                            "provider_id": "deepseek",
                                            "capabilities": [],
                                            "recommended_for": ["leader"],
                                            "selected": True,
                                        }
                                    ],
                                }
                            ],
                            "current": {
                                "provider_id": "deepseek",
                                "model_id": summary.model_name,
                            },
                        },
                        state,
                    )
                )
            models = self.client.list_models()
            return ChatMessage.system(_format_models(models, state))
        if command.kind is SlashCommandKind.THINKING:
            return ChatMessage.system(
                "\n".join(
                    [
                        "Thinking",
                        "",
                        "Current: on",
                        "Options",
                        "  On",
                        "  Off",
                        "",
                        "Interactive selection arrives in Task86.",
                    ]
                )
            )
        if command.kind is SlashCommandKind.MEMORY:
            memory = self.client.memory_summary()
            return ChatMessage.system(_format_memory(memory))
        if command.kind is SlashCommandKind.SKILLS:
            skills = self.client.list_skills()
            if not skills:
                return ChatMessage.system(
                    "Skills\n\nNo skills are available for the next turn."
                )
            return ChatMessage.system(
                "\n".join(
                    [
                        "Skills",
                        "",
                        "Apply one skill to the next turn:",
                        *[
                            "  "
                            + _label(
                                item,
                                "id",
                                suffix_keys=("version", "source_id", "risk_level"),
                            )
                            for item in skills
                        ],
                    ]
                )
            )
        if command.kind is SlashCommandKind.TOOLS:
            groups = self.client.list_tools()
            lines = ["Leader tools", ""]
            lines.extend(
                _format_tool_group(name, items) for name, items in groups.items()
            )
            return ChatMessage.system("\n".join(lines))
        if command.kind is SlashCommandKind.MCP:
            servers = self.client.mcp_status()
            if not servers:
                return ChatMessage.system("MCP servers\n\nNo MCP servers configured.")
            return ChatMessage.system(
                "\n".join(
                    [
                        "MCP servers",
                        "",
                        *(
                            _label(item, "id", suffix_keys=("status", "type", "trust"))
                            for item in servers
                        ),
                    ]
                )
            )
        if command.kind is SlashCommandKind.DETAILS:
            return ChatMessage.system("Details\n\n  Off\n  On")
        if command.kind is SlashCommandKind.USAGE:
            usage = self.client.usage_summary(
                state.backend_thread_id,
                state.current_run_id,
            )
            return ChatMessage.system(_format_usage(usage))
        if command.kind is SlashCommandKind.CONFIG:
            if state.first_run_summary is not None:
                summary = state.first_run_summary
                key_status = "set" if summary.model_api_key_configured else "missing"
                lines = [
                    "Configuration",
                    "",
                    "Project",
                    f"  Root: {summary.project_root}",
                    f"  Config: {summary.project_config}",
                    f"  Env: {summary.project_env}",
                    "Runtime",
                    f"  Home: {summary.home}",
                    f"  User config: {summary.user_config}",
                    "Secrets",
                    f"  {summary.model_api_key_env}: {key_status}",
                ]
                if not summary.model_api_key_configured:
                    guidance = missing_api_key_guidance(summary.model_api_key_env)
                    lines.extend(["", guidance.title, f"  {guidance.detail}"])
                    lines.extend(f"  Next: {step}" for step in guidance.next_steps)
                return ChatMessage.system("\n".join(lines))
            config = self.client.config_summary()
            return ChatMessage.system(_format_config(config))
        if command.kind is SlashCommandKind.NEW:
            target = command.argument or "New conversation"
            thread = self.client.create_thread(target)
            return ChatMessage.system(
                f"New conversation started: {_thread_label(thread)}",
                kind=ChatEventKind.RUN,
            )
        return ChatMessage.system(
            (
                f"Unknown command: /{command.argument}\n"
                "Type /help to see available commands."
            ),
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
    changed_file_count: int = 0
    latest_changed_files: tuple[ChangedFileSummary, ...] = ()


def thread_summaries(
    threads: Sequence[SurfaceThread | dict[str, object]],
    current_thread_id: str | None,
) -> list[ThreadSummary]:
    return [
        _thread_summary(thread, current_thread_id=current_thread_id)
        for thread in threads
    ]


def format_thread_list(threads: list[ThreadSummary]) -> str:
    if not threads:
        return "No conversations yet."
    lines = ["Conversations", ""]
    for thread in threads:
        marker = "*" if thread.current else " "
        updated = f"modified {thread.updated_label}" if thread.updated_label else "-"
        changes = _changed_file_count_label(thread.changed_file_count)
        lines.append(f"{marker} {thread.title:<24} {updated:<18} {changes}")
    return "\n".join(lines)


def _format_tool_group(name: str, items: list[dict[str, object]]) -> str:
    if not items:
        return f"{_display_group_name(name)}\n  -"
    rendered = [
        _label(item, "name", suffix_keys=("risk_level", "health")) for item in items
    ]
    return "\n".join([_display_group_name(name), *[f"  {item}" for item in rendered]])


def _display_group_name(name: str) -> str:
    normalized = name.replace("_", " ").strip()
    mapping = {
        "builtin": "Files",
        "sandbox": "Terminal",
        "mcp": "MCP",
        "approvals": "Approvals",
    }
    return mapping.get(normalized.casefold(), normalized.title() or "Other")


def _format_memory(memory: dict[str, object]) -> str:
    builtin = "on" if memory.get("builtin_enabled") is True else "off"
    provider_enabled = "on" if memory.get("provider_enabled") is True else "off"
    provider_status = memory.get("provider_status", "disabled")
    counts = (
        cast(dict[str, object], memory.get("counts"))
        if isinstance(memory.get("counts"), dict)
        else {}
    )
    files = (
        cast(dict[str, object], memory.get("files"))
        if isinstance(memory.get("files"), dict)
        else {}
    )
    return "\n".join(
        [
            "Memory",
            "",
            f"  Builtin: {builtin}",
            f"  Provider: {provider_enabled} ({provider_status})",
            f"  Entries: user={counts.get('user', 0)} memory={counts.get('memory', 0)}",
            f"  USER.md: {files.get('user', '-')}",
            f"  MEMORY.md: {files.get('memory', '-')}",
        ]
    )


def _format_usage(usage: dict[str, object]) -> str:
    return "\n".join(
        [
            "Usage",
            "",
            f"  Input tokens: {usage.get('input_tokens', usage.get('tokens', 0))}",
            f"  Output tokens: {usage.get('output_tokens', 0)}",
            f"  Reasoning tokens: {usage.get('reasoning_tokens', 0)}",
            f"  Token budget: {usage.get('budget', '-')}",
        ]
    )


def _format_config(config: dict[str, object]) -> str:
    return "\n".join(
        [
            "Configuration",
            "",
            "Project",
            f"  Root: {config.get('project_root', '-')}",
            "Runtime",
            f"  Mode: {config.get('mode', '-')}",
            f"  Home: {config.get('home', '-')}",
            "Model",
            f"  Default: {config.get('default_model', '-')}",
            "Memory",
            f"  Enabled: {config.get('memory_enabled', '-')}",
            "Sandbox",
            f"  Backend: {config.get('sandbox_backend', '-')}",
            "Secrets",
            f"  DeepSeek API key: {config.get('deepseek_api_key_configured', '-')}",
        ]
    )


def _format_models(catalog: dict[str, object], state: ChatSessionState) -> str:
    providers = catalog.get("providers")
    if not isinstance(providers, list) or not providers:
        return "Models\n\nNo providers configured.\nlast turn: none yet"
    lines = ["Models"]
    for raw_provider in providers:
        if not isinstance(raw_provider, dict):
            continue
        configured = "yes" if raw_provider.get("configured") is True else "no"
        provider_name = raw_provider.get("display_name") or raw_provider.get("id")
        line = f"Provider: {provider_name} configured={configured}"
        env_name = raw_provider.get("credential_env")
        present = "yes" if raw_provider.get("api_key_present") is True else "no"
        if env_name:
            line = f"{line} api_key_env={env_name} present={present}"
            if raw_provider.get("api_key_present") is not True:
                line = f"{line} (missing {env_name})"
        lines.append(line)
        models = raw_provider.get("models")
        if isinstance(models, list):
            for raw_model in models:
                if not isinstance(raw_model, dict):
                    continue
                selected = " *" if raw_model.get("selected") is True else ""
                display = raw_model.get("display_name") or raw_model.get("id")
                lines.append(f"  {display}{selected}")
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
            changed_file_count=thread.changed_file_count,
            latest_changed_files=thread.latest_changed_files,
        )
    thread_id = str(thread.get("id") or "-")
    title = str(thread.get("title") or thread_id)
    context = thread.get("context_path") or thread.get("context_label")
    updated = thread.get("updated_label")
    changed_file_count = thread.get("changed_file_count")
    return ThreadSummary(
        id=thread_id,
        short_id=thread_id[:8],
        title=title,
        current=thread_id == current_thread_id,
        context_label=str(context) if context is not None else None,
        updated_label=str(updated) if updated is not None else None,
        changed_file_count=(
            changed_file_count if isinstance(changed_file_count, int) else 0
        ),
    )


def _public_context_label(context_label: str | None) -> str:
    if not context_label:
        return "-"
    normalized = context_label.replace("\\", "/")
    if normalized.startswith("/mnt/user-data/"):
        return "workspace"
    return context_label


def _changed_file_count_label(count: int) -> str:
    if count == 0:
        return "no file changes"
    if count == 1:
        return "1 changed file"
    return f"{count} changed files"
