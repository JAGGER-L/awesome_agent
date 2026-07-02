from __future__ import annotations

from typing import ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import Input, Static

from awesome_agent.cli.config_flow import ConfigFlowSummary
from awesome_agent.cli.repo_context import CliLaunchContext
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
        launch_context: CliLaunchContext | None = None,
        first_run_summary: ConfigFlowSummary | None = None,
    ) -> None:
        super().__init__()
        self.api_url = api_url
        self.initial_run_id = run_id
        self.refresh_interval = refresh_interval
        self.client = client or TuiApiClient(api_url)
        self.state = ChatSessionState.new(
            launch_context=launch_context,
            first_run_summary=first_run_summary,
        )
        if run_id is not None:
            self.state = self.state.with_run(run_id)

    def compose(self) -> ComposeResult:
        with Vertical(id="chat-root"):
            yield Static("", id="welcome")
            yield Static("", id="transcript")
            yield Input(placeholder="Ask Awesome Agent, or type /help", id="prompt")
            yield Static("? for shortcuts - /help for commands", id="shortcuts")

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
                    (
                        f"Message received in {self.state.context_label}. "
                        "Coding Runs will inherit this context when started."
                    ),
                    kind=ChatEventKind.MESSAGE,
                )
            )
        else:
            try:
                if parsed.kind is SlashCommandKind.DETAILS:
                    self.state = self.state.toggle_details()
                    label = "enabled" if self.state.details_enabled else "disabled"
                    message = ChatMessage.system(f"Details {label}.")
                elif parsed.kind is SlashCommandKind.QUIT:
                    self.exit()
                    return
                else:
                    message = SlashRouter(self.client).handle(parsed, self.state)
            except Exception as error:
                message = ChatMessage.system(
                    str(error),
                    kind=ChatEventKind.ERROR,
                )
            self.state = self.state.append(message)
        self._render()

    def _render(self) -> None:
        self.query_one("#welcome", Static).update(self._welcome_text())
        self.query_one("#transcript", Static).update(
            "\n\n".join(render_message(message) for message in self.state.messages)
        )

    def _welcome_text(self) -> str:
        lines = [
            "+-- Awesome Agent --------------------------------------+",
            "| Welcome back                                          |",
            f"| cwd: {self.state.context_label}",
            "| tips: /help, /model, /status                          |",
        ]
        summary = self.state.first_run_summary
        if summary is not None and summary.needs_model_setup:
            lines.append(f"| setup: run awesome init; set {summary.model_api_key_env}")
        lines.append("+-------------------------------------------------------+")
        return "\n".join(lines)
