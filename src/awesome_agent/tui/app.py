from __future__ import annotations

from typing import ClassVar

from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import Input, Static

from awesome_agent.cli.config_flow import ConfigFlowSummary
from awesome_agent.cli.repo_context import CliLaunchContext
from awesome_agent.cli.slash_commands import SlashCommandKind, parse_slash_command
from awesome_agent.client.conversation import ConversationHttpError
from awesome_agent.conversation.events import ConversationStreamEventKind
from awesome_agent.tui.chat_state import ChatEventKind, ChatMessage, ChatSessionState
from awesome_agent.tui.client import TuiApiClient
from awesome_agent.tui.command_palette import CommandPaletteState, is_command_prefix
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

    #command-palette {
        max-height: 8;
    }

    #shortcuts {
        height: 1;
    }
    """
    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        ("ctrl+c", "cancel", "Cancel"),
        ("ctrl+r", "retry", "Retry"),
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
        self.command_palette = CommandPaletteState()
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
            yield Static("", id="command-palette")
            yield Input(placeholder="Ask Awesome Agent, or type /help", id="prompt")
            yield Static("? for shortcuts - /help for commands", id="shortcuts")

    def on_mount(self) -> None:
        self._render()
        self._focus_prompt()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        raw = self._active_command_value(event.value.strip())
        event.input.value = ""
        self.command_palette = self.command_palette.close()
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
                elif parsed.kind is SlashCommandKind.RUN:
                    message = self._start_coding_run(parsed.argument)
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

    def on_input_changed(self, event: Input.Changed) -> None:
        self.command_palette = self.command_palette.update(event.value)
        self._render_palette()

    def on_key(self, event: events.Key) -> None:
        if not self.command_palette.is_open:
            return
        if event.key == "escape":
            self.command_palette = self.command_palette.close()
            self._render_palette()
            event.prevent_default()
            event.stop()
            return
        if event.key in {"down", "ctrl+n"}:
            self.command_palette = self.command_palette.move(1)
            self._render_palette()
            event.prevent_default()
            event.stop()
            return
        if event.key in {"up", "ctrl+p"}:
            self.command_palette = self.command_palette.move(-1)
            self._render_palette()
            event.prevent_default()
            event.stop()
            return
        if event.key == "tab":
            active = self.command_palette.active
            if active is not None:
                prompt = self.query_one("#prompt", Input)
                self.command_palette = self.command_palette.close()
                self._render_palette()
                prompt.value = f"/{active.name} "
                prompt.cursor_position = len(prompt.value)
                event.prevent_default()
                event.stop()

    def action_cancel(self) -> None:
        if self.state.current_run_id is not None:
            try:
                cancelled = self.client.cancel(self.state.current_run_id)
                status = cancelled.get("status", "cancelled")
                message = ChatMessage.system(
                    f"Cancelled Run {self.state.current_run_id}: status={status}",
                    kind=ChatEventKind.RUN,
                )
                self.state = self.state.with_status(str(status)).append(message)
            except Exception as error:
                self.state = self.state.with_status("error").append(
                    ChatMessage.system(
                        self._format_error(error),
                        kind=ChatEventKind.ERROR,
                    )
                )
        elif self.state.status_label == "streaming":
            self.state = self.state.with_status("cancelled").append(
                ChatMessage.system(
                    "Cancel requested for the current conversation turn.",
                    kind=ChatEventKind.ERROR,
                )
            )
        else:
            self.state = self.state.append(ChatMessage.system("No active Run."))
        self._render()
        self._focus_prompt()

    def action_retry(self) -> None:
        content = self.state.last_failed_user_message
        if not content:
            self.state = self.state.append(
                ChatMessage.system(
                    "No failed conversation turn is available to retry.",
                    kind=ChatEventKind.ERROR,
                )
            )
            self._render()
            self._focus_prompt()
            return
        self.state = self.state.append(ChatMessage.system(f"Retrying: {content}"))
        self._send_user_message(content)
        self._render()
        self._focus_prompt()

    def _render(self) -> None:
        self.query_one("#welcome", Static).update(self._welcome_text())
        self.query_one("#transcript", Static).update(
            "\n\n".join(render_message(message) for message in self.state.messages)
        )
        self._render_palette()

    def _render_palette(self) -> None:
        self.query_one("#command-palette", Static).update(self.command_palette.render())

    def _active_command_value(self, raw: str) -> str:
        if not is_command_prefix(raw):
            return raw
        active = self.command_palette.active
        if active is None:
            return raw
        return f"/{active.name}"

    def _send_user_message(self, content: str) -> None:
        self.state = self.state.append(ChatMessage.user(content))
        self.state = self.state.with_status("streaming")
        self._render()
        failed = False
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
                    failed = True
                    message = self._format_stream_error(
                        stream_event.payload,
                        fallback="Conversation failed.",
                    )
                    self.state = self.state.append(
                        ChatMessage.system(str(message), kind=ChatEventKind.ERROR)
                    )
            self.state = self.state.with_status("error" if failed else "ready")
            self.state = self.state.with_last_failed_user_message(
                content if failed else None
            )
        except Exception as error:
            self.state = self.state.with_status("error")
            self.state = self.state.with_last_failed_user_message(content)
            self.state = self.state.append(
                ChatMessage.system(self._format_error(error), kind=ChatEventKind.ERROR)
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

    def _start_coding_run(self, goal: str) -> ChatMessage:
        if not goal:
            return ChatMessage.system(
                "Usage: /run <goal>",
                kind=ChatEventKind.ERROR,
            )
        thread_id = self._ensure_backend_thread(goal)
        context = self.state.launch_context
        repository_path = (
            context.display_path
            if context is not None and context.context_kind == "repo"
            else None
        )
        run = self.client.create_thread_run(
            thread_id,
            goal,
            repository_path=repository_path,
        )
        self.state = self.state.with_run(
            str(run["id"]), status_label=str(run["status"])
        )
        return ChatMessage.system(
            (f"Started Coding Run {run['id']}: {run['goal']} status={run['status']}"),
            kind=ChatEventKind.RUN,
        )

    def _focus_prompt(self) -> None:
        self.query_one("#prompt", Input).focus()

    def _format_error(self, error: Exception) -> str:
        message = str(error)
        if isinstance(error, ConversationHttpError):
            parts = [
                f"{error.code or 'http_error'}: {message}",
                f"status={error.status_code}",
            ]
            if error.request_id:
                parts.append(f"request_id={error.request_id}")
            if error.hint:
                parts.append(f"hint={error.hint}")
            if error.recoverable:
                parts.append("retryable=true")
            return " | ".join(parts)
        return message

    def _format_stream_error(
        self,
        payload: dict[str, object],
        *,
        fallback: str,
    ) -> str:
        message = payload.get("message")
        text = message if isinstance(message, str) else fallback
        code = payload.get("code")
        hint = payload.get("hint")
        retryable = payload.get("retryable")
        action_required = (
            payload.get("approval_required") is True
            or payload.get("interrupt") is True
            or code in {"approval_required", "interrupt"}
        )
        prefix = "Action required" if action_required else None
        parts = [f"{code}: {text}" if isinstance(code, str) else text]
        if prefix is not None:
            parts[0] = f"{prefix}: {parts[0]}"
        if isinstance(hint, str):
            parts.append(f"hint={hint}")
        if retryable is True:
            parts.append("retryable=true")
        return " | ".join(parts)

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
