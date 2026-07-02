from __future__ import annotations

from typing import ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import Input, Static

from awesome_agent.cli.config_flow import ConfigFlowSummary
from awesome_agent.cli.repo_context import CliLaunchContext
from awesome_agent.cli.slash_commands import SlashCommandKind, parse_slash_command
from awesome_agent.conversation.events import ConversationStreamEventKind
from awesome_agent.tui.chat_state import ChatEventKind, ChatMessage, ChatSessionState
from awesome_agent.tui.client import TuiApiClient
from awesome_agent.tui.rendering import render_message
from awesome_agent.tui.slash_router import SlashRouter


class AwesomeAgentTui(App[None]):
    TITLE = "awesome_agent"
    SUB_TITLE = "Chat-first local coding agent"
    CSS = """
    #chat-root {
        height: 100%;
    }

    #transcript {
        height: 1fr;
        overflow-y: auto;
    }

    #prompt {
        height: 3;
    }

    #shortcuts {
        height: 1;
    }
    """
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
        self._focus_prompt()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        raw = event.value.strip()
        event.input.value = ""
        if not raw:
            return
        parsed = parse_slash_command(raw)
        if parsed.kind is SlashCommandKind.USER_MESSAGE:
            self._send_user_message(raw)
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
        self._focus_prompt()

    def _render(self) -> None:
        self.query_one("#welcome", Static).update(self._welcome_text())
        self.query_one("#transcript", Static).update(
            "\n\n".join(render_message(message) for message in self.state.messages)
        )

    def _send_user_message(self, content: str) -> None:
        self.state = self.state.append(ChatMessage.user(content))
        self.state = self.state.with_status("streaming")
        self._render()
        try:
            thread_id = self._ensure_backend_thread(content)
            assistant_buffer = ""
            for stream_event in self.client.stream_turn(thread_id, content):
                if stream_event.event is ConversationStreamEventKind.MESSAGE_DELTA:
                    text = stream_event.payload.get("text")
                    if isinstance(text, str):
                        assistant_buffer += text
                        self.state = self.state.upsert_streaming_assistant(
                            assistant_buffer
                        )
                        self._render()
                elif (
                    stream_event.event is ConversationStreamEventKind.MESSAGE_COMPLETED
                ):
                    final_content = stream_event.payload.get("content")
                    if isinstance(final_content, str):
                        assistant_buffer = final_content
                    self.state = self.state.upsert_streaming_assistant(assistant_buffer)
                elif stream_event.event is ConversationStreamEventKind.ERROR:
                    message = (
                        stream_event.payload.get("message") or "Conversation failed."
                    )
                    self.state = self.state.append(
                        ChatMessage.system(str(message), kind=ChatEventKind.ERROR)
                    )
            self.state = self.state.with_status("ready")
        except Exception as error:
            self.state = self.state.with_status("error")
            self.state = self.state.append(
                ChatMessage.system(str(error), kind=ChatEventKind.ERROR)
            )

    def _ensure_backend_thread(self, title_seed: str) -> str:
        if self.state.backend_thread_id is not None:
            return self.state.backend_thread_id
        context = self.state.launch_context
        thread = self.client.create_thread(
            title=title_seed[:80] or "New conversation",
            context_kind=context.context_kind if context is not None else None,
            context_path=context.display_path if context is not None else None,
        )
        thread_id = str(thread["id"])
        self.state = self.state.with_backend_thread(thread_id)
        return thread_id

    def _focus_prompt(self) -> None:
        self.query_one("#prompt", Input).focus()

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
