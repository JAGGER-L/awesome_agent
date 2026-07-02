from __future__ import annotations

from typing import Protocol

from awesome_agent.cli.slash_commands import (
    SlashCommand,
    SlashCommandKind,
    slash_command_help,
)
from awesome_agent.tui.chat_state import ChatEventKind, ChatMessage, ChatSessionState


class ChatSemanticClient(Protocol):
    def create_thread(self, title: str) -> dict[str, object]: ...

    def runtime_status(self) -> dict[str, object]: ...

    def list_models(self) -> list[dict[str, object]]: ...

    def memory_summary(self) -> dict[str, object]: ...

    def list_threads(self) -> list[dict[str, object]]: ...

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
            if not threads:
                return ChatMessage.system("No API-backed threads found.")
            return ChatMessage.system(
                "\n".join(str(item.get("title") or item.get("id")) for item in threads)
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
                suffix = (
                    "configured"
                    if summary.model_api_key_configured
                    else f"missing {summary.model_api_key_env}"
                )
                return ChatMessage.system(f"default: {summary.model_name} ({suffix})")
            models = self.client.list_models()
            lines = [
                f"{item.get('role', 'model')}: {item.get('name')}" for item in models
            ]
            return ChatMessage.system("\n".join(lines) or "No models configured.")
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
            logical_workspace = (
                thread.get("logical_workspace_path")
                or thread.get("logical_workspace")
                or "/mnt/user-data/workspace/"
            )
            return ChatMessage.system(
                (
                    f"Started thread {thread['id']}: {thread.get('title', target)}\n"
                    f"workspace={logical_workspace}"
                ),
                kind=ChatEventKind.RUN,
            )
        return ChatMessage.system(
            f"Unknown command. Try /help. Current thread={state.thread_id}",
            kind=ChatEventKind.ERROR,
        )


def _format_tool_group(name: str, items: list[dict[str, object]]) -> str:
    if not items:
        return f"{name}: -"
    rendered = [
        _label(item, "name", suffix_keys=("risk_level", "health")) for item in items
    ]
    return f"{name}: {', '.join(rendered)}"


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
