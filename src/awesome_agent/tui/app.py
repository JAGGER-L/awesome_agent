from __future__ import annotations

from typing import ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import Footer, Header, Input, Static

from awesome_agent.cli.slash_commands import SlashCommandKind, parse_slash_command
from awesome_agent.tui.chat_state import ChatEventKind, ChatMessage, ChatSessionState
from awesome_agent.tui.client import TuiApiClient
from awesome_agent.tui.rendering import render_message
from awesome_agent.tui.slash_router import SlashRouter


class AwesomeAgentTui(App[None]):
    TITLE = "awesome_agent"
    SUB_TITLE = "Chat-first local coding agent"
    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        ("q", "quit", "Quit"),
    ]

    def __init__(
        self,
        *,
        api_url: str,
        run_id: str | None = None,
        refresh_interval: float = 2.0,
        client: TuiApiClient | None = None,
    ) -> None:
        super().__init__()
        self.api_url = api_url
        self.initial_run_id = run_id
        self.refresh_interval = refresh_interval
        self.client = client or TuiApiClient(api_url)
        self.state = ChatSessionState.new()
        if run_id is not None:
            self.state = self.state.with_run(run_id)

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="chat-root"):
            yield Static("", id="status-strip")
            yield Static("", id="transcript")
            yield Input(placeholder="Ask awesome_agent, or type /help", id="prompt")
        yield Footer()

    def on_mount(self) -> None:
        self._render()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        raw = event.value.strip()
        event.input.value = ""
        if not raw:
            return
        parsed = parse_slash_command(raw)
        if parsed.kind is SlashCommandKind.USER_MESSAGE:
            self.state = self.state.append(ChatMessage.user(raw))
            self.state = self.state.append(
                ChatMessage.system(
                    "Select repository context to create a Run in the current thread.",
                    kind=ChatEventKind.RUN,
                )
            )
        else:
            try:
                message = SlashRouter(self.client).handle(parsed, self.state)
            except Exception as error:
                message = ChatMessage.system(
                    str(error),
                    kind=ChatEventKind.ERROR,
                )
            self.state = self.state.append(message)
        self._render()

    def _render(self) -> None:
        self.query_one("#status-strip", Static).update(
            " ".join(
                [
                    f"thread={self.state.thread_id}",
                    f"run={self.state.current_run_id or '-'}",
                    f"status={self.state.status_label}",
                ]
            )
        )
        self.query_one("#transcript", Static).update(
            "\n\n".join(render_message(message) for message in self.state.messages)
        )
