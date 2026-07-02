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
        if command.kind is SlashCommandKind.STATUS:
            status = self.client.runtime_status()
            return ChatMessage.system(
                " ".join(f"{key}={value}" for key, value in status.items()),
                kind=ChatEventKind.RUN,
            )
        if command.kind is SlashCommandKind.MODELS:
            models = self.client.list_models()
            lines = [
                f"{item.get('role', 'model')}: {item.get('name')}"
                for item in models
            ]
            return ChatMessage.system("\n".join(lines) or "No models configured.")
        if command.kind is SlashCommandKind.MEMORY:
            memory = self.client.memory_summary()
            return ChatMessage.system(
                " ".join(f"{key}={value}" for key, value in memory.items())
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
