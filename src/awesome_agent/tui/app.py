from __future__ import annotations

from contextlib import suppress
from typing import ClassVar
from uuid import uuid4

from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import Input, Static
from textual.worker import Worker

from awesome_agent.cli.config_flow import ConfigFlowSummary
from awesome_agent.cli.repo_context import CliLaunchContext
from awesome_agent.cli.slash_commands import (
    SlashCommand,
    SlashCommandKind,
    parse_slash_command,
)
from awesome_agent.client.conversation import ConversationHttpError
from awesome_agent.conversation.events import (
    ConversationStreamEvent,
    ConversationStreamEventKind,
)
from awesome_agent.surfaces.client import SurfaceClient, SurfaceThread
from awesome_agent.tui.chat_state import (
    ChatEventKind,
    ChatMessage,
    ChatSessionState,
    chat_messages_from_thread_records,
    should_resume_last_run,
)
from awesome_agent.tui.client import HttpSurfaceClient
from awesome_agent.tui.command_palette import CommandPaletteState, is_command_prefix
from awesome_agent.tui.rendering import render_transcript
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
        ("ctrl+o", "toggle_thought", "Toggle thought"),
        ("ctrl+r", "retry", "Retry"),
        ("q", "quit", "Quit"),
    ]

    def __init__(
        self,
        *,
        api_url: str | None = None,
        run_id: str | None = None,
        refresh_interval: float = 2.0,
        client: SurfaceClient | None = None,
        launch_context: CliLaunchContext | None = None,
        first_run_summary: ConfigFlowSummary | None = None,
    ) -> None:
        super().__init__()
        self.api_url = api_url
        self.initial_run_id = run_id
        self.refresh_interval = refresh_interval
        if client is None:
            if api_url is None:
                from awesome_agent.surfaces.local_client import LocalSurfaceClient

                client = LocalSurfaceClient()
            else:
                client = HttpSurfaceClient(api_url)
        self.client = client
        self.command_palette = CommandPaletteState()
        self.state = ChatSessionState.new(
            launch_context=launch_context,
            first_run_summary=first_run_summary,
        )
        if run_id is not None:
            self.state = self.state.with_run(run_id)
        self._active_worker: Worker[object] | None = None

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
            resume_run_id = (
                self.state.last_resumable_run_id
                if should_resume_last_run(raw)
                else None
            )
            self._start_user_message(raw, resume_run_id=resume_run_id)
        else:
            if parsed.kind is SlashCommandKind.DETAILS:
                self.state = self.state.toggle_details()
                label = "enabled" if self.state.details_enabled else "disabled"
                self.state = self.state.append(ChatMessage.system(f"Details {label}."))
            elif parsed.kind is SlashCommandKind.QUIT:
                self.exit()
                return
            elif (
                parsed.kind is SlashCommandKind.RESUME
                and not parsed.argument
                and self.state.last_resumable_run_id is not None
            ):
                self._start_user_message(
                    "continue",
                    resume_run_id=self.state.last_resumable_run_id,
                )
            else:
                self._start_command(parsed)
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
        if self.state.active_operation_id is not None:
            if self._active_worker is not None:
                self._active_worker.cancel()
            resumable_run_id = (
                self.state.current_run_id or self.state.active_operation_id
            )
            if self.state.current_run_id is not None:
                with suppress(Exception):
                    self.client.cancel(self.state.current_run_id)
            self.state = self.state.mark_operation_paused(resumable_run_id).append(
                ChatMessage.system(
                    'Response paused. Type "continue" or /resume to continue.',
                    kind=ChatEventKind.RUN,
                )
            )
        elif self.state.current_run_id is not None:
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
        self._start_user_message(content)
        self._render()
        self._focus_prompt()

    def action_toggle_thought(self) -> None:
        self.state = self.state.toggle_thought()
        self._render()
        self._focus_prompt()

    def _render(self) -> None:
        self.query_one("#welcome", Static).update(self._welcome_text())
        self.query_one("#transcript", Static).update(
            render_transcript(self.state.messages, thought=self.state.thought_block())
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

    def _start_user_message(
        self,
        content: str,
        *,
        resume_run_id: str | None = None,
    ) -> None:
        if self.state.active_operation_id is not None:
            self.state = self.state.append(
                ChatMessage.system(
                    "Finish or pause the current response first.",
                    kind=ChatEventKind.ERROR,
                )
            )
            return
        self.state = self.state.append(ChatMessage.user(content))
        self.state = self.state.begin_operation(str(uuid4()), "streaming")
        self._render()
        try:
            thread_id = self._ensure_backend_thread(content)
            self._active_worker = self.run_worker(
                lambda: self._conversation_worker(
                    thread_id,
                    content,
                    resume_run_id,
                ),
                thread=True,
                name=f"conversation-{self.state.active_operation_id}",
            )
        except Exception as error:
            self.state = self.state.with_status("error")
            self.state = self.state.with_last_failed_user_message(content)
            self.state = self.state.append(
                ChatMessage.system(self._format_error(error), kind=ChatEventKind.ERROR)
            )

    def _conversation_worker(
        self,
        thread_id: str,
        content: str,
        resume_run_id: str | None,
    ) -> None:
        failed = False
        try:
            for stream_event in self.client.stream_turn(
                thread_id,
                content,
                resume_run_id=resume_run_id,
            ):
                if stream_event.event is ConversationStreamEventKind.ERROR:
                    failed = True
                self.call_from_thread(self._apply_stream_event, stream_event)
        except Exception as error:
            failed = True
            self.call_from_thread(self._record_stream_exception, content, error)
        finally:
            self.call_from_thread(
                self._finish_stream_worker,
                content,
                failed=failed,
            )

    def _apply_stream_event(self, stream_event: ConversationStreamEvent) -> None:
        run_id = stream_event.payload.get("run_id")
        if isinstance(run_id, str):
            self.state = self.state.note_run_started(run_id)
        if stream_event.event is ConversationStreamEventKind.REASONING_STARTED:
            self.state = self.state.begin_thought(stream_event.created_at)
        elif stream_event.event is ConversationStreamEventKind.REASONING_DELTA:
            text = stream_event.payload.get("text")
            if isinstance(text, str):
                self.state = self.state.append_thought_delta(text)
        elif stream_event.event is ConversationStreamEventKind.REASONING_COMPLETED:
            self.state = self.state.complete_thought(stream_event.created_at)
        elif stream_event.event is ConversationStreamEventKind.MESSAGE_DELTA:
            text = stream_event.payload.get("text")
            if isinstance(text, str):
                self.state = self.state.append_stream_delta(text)
        elif stream_event.event is ConversationStreamEventKind.MESSAGE_COMPLETED:
            final_content = stream_event.payload.get("content")
            if isinstance(final_content, str):
                self.state = self.state.upsert_streaming_assistant(final_content)
        elif stream_event.event is ConversationStreamEventKind.ERROR:
            message = self._format_stream_error(
                stream_event.payload,
                fallback="Conversation failed.",
            )
            self.state = self.state.append(
                ChatMessage.system(str(message), kind=ChatEventKind.ERROR)
            )
        self._render()
        self._focus_prompt()

    def _record_stream_exception(self, content: str, error: Exception) -> None:
        self.state = self.state.with_last_failed_user_message(content)
        self.state = self.state.append(
            ChatMessage.system(self._format_error(error), kind=ChatEventKind.ERROR)
        )
        self._render()
        self._focus_prompt()

    def _finish_stream_worker(self, content: str, *, failed: bool) -> None:
        self._active_worker = None
        self.state = self.state.finish_operation(
            status_label="error" if failed else "ready"
        )
        self.state = self.state.with_last_failed_user_message(
            content if failed else None
        )
        self._render()
        self._focus_prompt()

    def _start_command(self, parsed: SlashCommand) -> None:
        if self.state.active_operation_id is not None:
            self.state = self.state.append(
                ChatMessage.system(
                    "Finish or pause the current response first.",
                    kind=ChatEventKind.ERROR,
                )
            )
            return
        self.state = self.state.begin_operation(str(uuid4()), "command")
        state_snapshot = self.state
        self._active_worker = self.run_worker(
            lambda: self._command_worker(parsed, state_snapshot),
            thread=True,
            name=f"command-{self.state.active_operation_id}",
        )

    def _command_worker(
        self,
        parsed: SlashCommand,
        state: ChatSessionState,
    ) -> None:
        failed = False
        try:
            if parsed.kind is SlashCommandKind.RUN:
                backend_thread_id, run, message = self._start_coding_run(parsed, state)
                self.call_from_thread(
                    self._append_coding_run_message,
                    backend_thread_id,
                    run,
                    message,
                )
            elif parsed.kind is SlashCommandKind.NEW:
                thread, message = self._create_thread(parsed, state)
                self.call_from_thread(
                    self._switch_to_thread,
                    thread,
                    [],
                    message,
                )
            elif parsed.kind is SlashCommandKind.RESUME:
                if not parsed.argument:
                    guidance = (
                        "Use /threads to choose a conversation or "
                        "/resume <id-or-title>."
                    )
                    message = ChatMessage.system(guidance)
                    self.call_from_thread(self._append_command_message, message)
                else:
                    thread = self.client.resume_thread(parsed.argument)
                    messages = chat_messages_from_thread_records(
                        self.client.list_thread_messages(_thread_id(thread))
                    )
                    message = ChatMessage.system(
                        f"Resumed conversation: {_thread_title(thread)}",
                        kind=ChatEventKind.RUN,
                    )
                    self.call_from_thread(
                        self._switch_to_thread,
                        thread,
                        messages,
                        message,
                    )
            else:
                message = SlashRouter(self.client).handle(parsed, state)
                self.call_from_thread(self._append_command_message, message)
        except Exception as error:
            failed = True
            self.call_from_thread(
                self._append_command_message,
                ChatMessage.system(str(error), kind=ChatEventKind.ERROR),
            )
        finally:
            self.call_from_thread(
                self._finish_command_worker,
                failed=failed,
            )

    def _append_command_message(self, message: ChatMessage) -> None:
        self.state = self.state.append(message)
        self._render()
        self._focus_prompt()

    def _append_coding_run_message(
        self,
        backend_thread_id: str | None,
        run: dict[str, object] | None,
        message: ChatMessage,
    ) -> None:
        if backend_thread_id is not None:
            self.state = self.state.with_backend_thread(backend_thread_id)
        if run is not None:
            self.state = self.state.with_run(
                str(run["id"]), status_label=str(run["status"])
            )
        self.state = self.state.append(message)
        self._render()
        self._focus_prompt()

    def _switch_to_thread(
        self,
        thread: SurfaceThread | dict[str, object],
        messages: list[ChatMessage],
        message: ChatMessage,
    ) -> None:
        self.state = self.state.switch_thread(
            backend_thread_id=_thread_id(thread),
            title=_thread_title(thread),
            context_label=_thread_context_label(thread),
            messages=messages,
        )
        self.state = self.state.append(message)
        self._render()
        self._focus_prompt()

    def _finish_command_worker(self, *, failed: bool) -> None:
        self._active_worker = None
        self.state = self.state.finish_operation(
            status_label="error" if failed else "ready"
        )
        self._render()
        self._focus_prompt()

    def _ensure_backend_thread(self, title_seed: str) -> str:
        if self.state.backend_thread_id is not None:
            return self.state.backend_thread_id
        context = self.state.launch_context
        thread = self.client.create_thread(
            title=title_seed[:80] or "New conversation",
            context_kind=context.context_kind if context is not None else None,
            context_path=context.display_path if context is not None else None,
        )
        thread_id = _thread_id(thread)
        self.state = self.state.with_backend_thread(
            thread_id,
            title=_thread_title(thread),
            context_label=_thread_context_label(thread),
        )
        return thread_id

    def _create_thread(
        self,
        parsed: SlashCommand,
        state: ChatSessionState,
    ) -> tuple[SurfaceThread | dict[str, object], ChatMessage]:
        title = parsed.argument or "New conversation"
        context = state.launch_context
        thread = self.client.create_thread(
            title=title,
            context_kind=context.context_kind if context is not None else None,
            context_path=context.display_path if context is not None else None,
        )
        return (
            thread,
            ChatMessage.system(
                f"New conversation started: {_thread_title(thread)}",
                kind=ChatEventKind.RUN,
            ),
        )

    def _start_coding_run(
        self,
        parsed: SlashCommand,
        state: ChatSessionState,
    ) -> tuple[str | None, dict[str, object] | None, ChatMessage]:
        goal = parsed.argument
        if not goal:
            return (
                None,
                None,
                ChatMessage.system(
                    "Usage: /run <goal>",
                    kind=ChatEventKind.ERROR,
                ),
            )
        thread_id = state.backend_thread_id
        new_backend_thread_id: str | None = None
        if thread_id is None:
            context = state.launch_context
            thread = self.client.create_thread(
                title=goal[:80] or "New conversation",
                context_kind=context.context_kind if context is not None else None,
                context_path=context.display_path if context is not None else None,
            )
            thread_id = _thread_id(thread)
            new_backend_thread_id = thread_id
        context = state.launch_context
        repository_path = (
            context.display_path
            if context is not None and context.context_kind == "repo"
            else None
        )
        if hasattr(self.client, "start_explicit_run"):
            run = self.client.start_explicit_run(
                thread_id,
                goal,
                repository_path=repository_path,
            )
        else:
            run = self.client.create_thread_run(  # type: ignore[attr-defined]
                thread_id,
                goal,
                repository_path=repository_path,
            )
        return (
            new_backend_thread_id,
            run,
            ChatMessage.system(
                (
                    f"Started Coding Run {run['id']}: "
                    f"{run['goal']} status={run['status']}"
                ),
                kind=ChatEventKind.RUN,
            ),
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
        if self.state.messages:
            return ""
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


def _thread_id(thread: SurfaceThread | dict[str, object]) -> str:
    if isinstance(thread, SurfaceThread):
        return thread.id
    return str(thread["id"])


def _thread_title(thread: SurfaceThread | dict[str, object]) -> str:
    if isinstance(thread, SurfaceThread):
        return thread.title
    return str(thread.get("title") or "New conversation")


def _thread_context_label(thread: SurfaceThread | dict[str, object]) -> str | None:
    if isinstance(thread, SurfaceThread):
        return thread.context_label
    context = thread.get("context_path") or thread.get("context_label")
    return str(context) if context is not None else None
