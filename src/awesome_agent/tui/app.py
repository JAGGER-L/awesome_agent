from __future__ import annotations

from contextlib import suppress
from typing import ClassVar, cast
from uuid import UUID, uuid4

from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.css.query import NoMatches
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
from awesome_agent.surfaces.client import (
    SurfaceClient,
    SurfaceThread,
    changed_file_summaries_from_payload,
)
from awesome_agent.tui.chat_state import (
    ChatEventKind,
    ChatMessage,
    ChatSessionState,
    chat_messages_from_thread_records,
    should_resume_last_run,
)
from awesome_agent.tui.client import HttpSurfaceClient
from awesome_agent.tui.command_palette import CommandPaletteState, is_command_prefix
from awesome_agent.tui.events import (
    ApprovalPromptState,
    TeamDisplayEvent,
    ToolDisplayEvent,
)
from awesome_agent.tui.pickers import PickerItem, PickerState
from awesome_agent.tui.rendering import (
    render_approval_prompt,
    render_changed_files,
    render_team_event,
    render_tool_event,
    render_transcript,
)
from awesome_agent.tui.slash_router import SlashRouter


class AwesomeAgentTui(App[None]):
    TITLE = "awesome_agent"
    SUB_TITLE = "Chat-first local coding agent"
    CSS = """
    #chat-root {
        height: 100%;
    }

    #transcript-scroll {
        height: 1fr;
        overflow-y: auto;
    }

    #transcript {
        width: 100%;
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
        resolved_client: SurfaceClient | None = client
        if resolved_client is None:
            if api_url is None:
                from awesome_agent.surfaces.local_client import LocalSurfaceClient

                resolved_client = LocalSurfaceClient()
            else:
                resolved_client = cast(SurfaceClient, HttpSurfaceClient(api_url))
        self.client: SurfaceClient = resolved_client
        self.command_palette = CommandPaletteState()
        self.state = ChatSessionState.new(
            launch_context=launch_context,
            first_run_summary=first_run_summary,
        )
        if run_id is not None:
            self.state = self.state.with_run(run_id)
        self._active_worker: Worker[object] | None = None
        self._seen_runtime_events: set[tuple[str, int]] = set()
        self._last_runtime_sequence_by_run: dict[str, int] = {}

    def compose(self) -> ComposeResult:
        with Vertical(id="chat-root"):
            yield Static("", id="welcome")
            with VerticalScroll(id="transcript-scroll"):
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
            if self.state.pending_approval is not None:
                self._apply_approval_choice(self.state.pending_approval.active_index)
            return
        if self.state.pending_approval is not None:
            if raw in {"1", "2", "3"}:
                self._apply_approval_choice(int(raw) - 1)
            else:
                self.state = self.state.append(
                    ChatMessage.system(
                        "Decide the pending approval before sending another message.",
                        kind=ChatEventKind.ERROR,
                    )
                )
                self._render()
                self._focus_prompt()
            return
        parsed = parse_slash_command(raw)
        if parsed.kind is SlashCommandKind.USER_MESSAGE:
            if should_resume_last_run(raw):
                self._start_continue_turn(
                    expected_run_id=self.state.last_resumable_run_id
                )
            else:
                self._start_user_message(raw)
        else:
            if parsed.kind is not SlashCommandKind.QUIT:
                self.state = self.state.append(ChatMessage.command(raw))
            if parsed.kind is SlashCommandKind.DETAILS:
                self.state = self.state.toggle_details()
                label = "enabled" if self.state.details_enabled else "disabled"
                self.state = self.state.append(ChatMessage.system(f"Details {label}."))
            elif parsed.kind is SlashCommandKind.QUIT:
                self.exit()
                return
            elif parsed.kind in {
                SlashCommandKind.MODEL,
                SlashCommandKind.THINKING,
                SlashCommandKind.MEMORY,
                SlashCommandKind.SKILLS,
                SlashCommandKind.THREADS,
            }:
                self._open_picker(parsed)
            else:
                self._start_command(parsed)
        self._render()
        self._focus_prompt()

    def on_input_changed(self, event: Input.Changed) -> None:
        self.command_palette = self.command_palette.update(event.value)
        self._render_palette()

    def on_key(self, event: events.Key) -> None:
        pending_approval = self.state.pending_approval
        if pending_approval is not None:
            if event.key in {"down", "ctrl+n"}:
                self.state = self.state.with_approval_prompt(pending_approval.move(1))
                self._render()
                event.prevent_default()
                event.stop()
                return
            if event.key in {"up", "ctrl+p"}:
                self.state = self.state.with_approval_prompt(pending_approval.move(-1))
                self._render()
                event.prevent_default()
                event.stop()
                return
            if event.key in {"1", "2", "3"}:
                self._apply_approval_choice(int(event.key) - 1)
                event.prevent_default()
                event.stop()
                return
            if event.key == "enter":
                self._apply_approval_choice(pending_approval.active_index)
                event.prevent_default()
                event.stop()
                return
        active_picker = self.state.active_picker
        if active_picker is not None:
            if event.key == "escape":
                self.state = self.state.close_picker()
                self._render()
                event.prevent_default()
                event.stop()
                return
            if event.key in {"down", "ctrl+n"}:
                self.state = self.state.open_picker(active_picker.move(1))
                self._render()
                event.prevent_default()
                event.stop()
                return
            if event.key in {"up", "ctrl+p"}:
                self.state = self.state.open_picker(active_picker.move(-1))
                self._render()
                event.prevent_default()
                event.stop()
                return
            if event.key == "enter":
                self._apply_picker()
                event.prevent_default()
                event.stop()
                return
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
            if self.state.current_run_id is not None:
                with suppress(Exception):
                    self.client.cancel(
                        self.state.current_run_id,
                        thread_id=self.state.backend_thread_id,
                    )
            self.state = self.state.with_status("cancelling").append(
                ChatMessage.system(
                    "Cancellation requested. Waiting for the runtime to stop safely.",
                    kind=ChatEventKind.RUN,
                )
            )
        elif self.state.current_run_id is not None:
            try:
                cancelled = self.client.cancel(
                    self.state.current_run_id,
                    thread_id=self.state.backend_thread_id,
                )
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

    def _render(self, *, follow: bool = True) -> None:
        try:
            self.query_one("#welcome", Static).update(self._welcome_text())
            self.query_one("#transcript", Static).update(
                render_transcript(
                    self.state.messages,
                    thought_blocks=self.state.thought_blocks,
                )
            )
        except NoMatches:
            return
        self._render_palette()
        if follow:
            self.call_after_refresh(self._scroll_transcript_end)

    def _scroll_transcript_end(self) -> None:
        try:
            self.query_one("#transcript-scroll", VerticalScroll).scroll_end(
                animate=False
            )
        except NoMatches:
            return
        self._focus_prompt()

    def _render_palette(self) -> None:
        try:
            if self.state.pending_approval is not None:
                self.query_one("#command-palette", Static).update(
                    render_approval_prompt(self.state.pending_approval)
                )
                return
            if self.state.active_picker is not None:
                self.query_one("#command-palette", Static).update(
                    self.state.active_picker.render()
                )
                return
            self.query_one("#command-palette", Static).update(
                self.command_palette.render()
            )
        except NoMatches:
            return

    def _active_command_value(self, raw: str) -> str:
        if not is_command_prefix(raw):
            return raw
        active = self.command_palette.active
        if active is None:
            return raw
        return f"/{active.name}"

    def _start_user_message(self, content: str) -> None:
        if self.state.active_operation_id is not None:
            self.state = self.state.append(
                ChatMessage.system(
                    "Finish or pause the current response first.",
                    kind=ChatEventKind.ERROR,
                )
            )
            return
        turn_id = str(uuid4())
        self.state = self.state.begin_operation(turn_id, "streaming")
        self.state = self.state.begin_turn(turn_id)
        self.state = self.state.append(ChatMessage.user(content, turn_id=turn_id))
        self._render()
        try:
            thread_id = self._ensure_backend_thread(content)
            self._active_worker = self.run_worker(
                lambda: self._conversation_worker(
                    thread_id,
                    content,
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
    ) -> None:
        failed = False
        try:
            for stream_event in self.client.stream_turn(
                thread_id,
                content,
                model=self.state.current_model,
                thinking=self.state.thinking_mode,
                memory={
                    "local_enabled": self.state.local_memory_enabled,
                    "provider": self.state.provider_memory,
                },
                skill_ids=self.state.staged_skill_ids,
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

    def _start_continue_turn(self, *, expected_run_id: str | None = None) -> None:
        thread_id = self.state.backend_thread_id
        if thread_id is None:
            self.state = self.state.append(
                ChatMessage.system(
                    "No active conversation is available to continue.",
                    kind=ChatEventKind.ERROR,
                )
            )
            return
        if self.state.active_operation_id is not None:
            self.state = self.state.append(
                ChatMessage.system(
                    "Finish or pause the current response first.",
                    kind=ChatEventKind.ERROR,
                )
            )
            return
        operation_id = str(uuid4())
        self.state = self.state.begin_operation(operation_id, "continuing")
        self._active_worker = self.run_worker(
            lambda: self._continue_worker(thread_id, expected_run_id),
            thread=True,
            name=f"continue-{operation_id}",
        )

    def _continue_worker(self, thread_id: str, expected_run_id: str | None) -> None:
        failed = False
        try:
            for stream_event in self.client.continue_turn(
                thread_id,
                expected_run_id=expected_run_id,
                after_sequence=self._last_runtime_sequence(expected_run_id),
            ):
                if stream_event.event is ConversationStreamEventKind.ERROR:
                    failed = True
                self.call_from_thread(self._apply_stream_event, stream_event)
        except Exception as error:
            failed = True
            self.call_from_thread(self._record_continue_exception, error)
        finally:
            self.call_from_thread(
                self._finish_stream_worker,
                "",
                failed=failed,
            )

    def _apply_stream_event(self, stream_event: ConversationStreamEvent) -> None:
        if not self._remember_stream_event(stream_event):
            return
        run_id = _stream_event_run_id(stream_event)
        if run_id is not None:
            self.state = self.state.note_run_started(run_id)
        if stream_event.event is ConversationStreamEventKind.TURN_CONTINUED:
            self.state = self.state.begin_turn(str(stream_event.turn_id))
        elif stream_event.event is ConversationStreamEventKind.REASONING_STARTED:
            if self.state.thinking_mode == "off":
                return
            self.state = self.state.begin_thought(stream_event.created_at)
        elif stream_event.event is ConversationStreamEventKind.REASONING_DELTA:
            if self.state.thinking_mode == "off":
                return
            text = stream_event.payload.get("text")
            if isinstance(text, str):
                self.state = self.state.append_thought_delta(text)
        elif stream_event.event is ConversationStreamEventKind.REASONING_COMPLETED:
            if self.state.thinking_mode == "off":
                return
            self.state = self.state.complete_thought(stream_event.created_at)
        elif stream_event.event is ConversationStreamEventKind.MESSAGE_DELTA:
            text = stream_event.payload.get("text")
            if isinstance(text, str):
                self.state = self.state.append_stream_delta(text)
        elif stream_event.event in {
            ConversationStreamEventKind.TOOL_STARTED,
            ConversationStreamEventKind.TOOL_PROGRESS,
            ConversationStreamEventKind.TOOL_COMPLETED,
        }:
            self.state = self.state.append(
                ChatMessage.system(
                    render_tool_event(
                        _tool_display_event(stream_event.payload),
                        details_enabled=self.state.details_enabled,
                    ).plain,
                    kind=ChatEventKind.TOOL,
                )
            )
        elif stream_event.event is ConversationStreamEventKind.TEAM_EVENT:
            self.state = self.state.append(
                ChatMessage.system(
                    render_team_event(
                        _team_display_event(stream_event.payload),
                        details_enabled=self.state.details_enabled,
                    ).plain,
                    kind=ChatEventKind.RUN,
                )
            )
        elif stream_event.event is ConversationStreamEventKind.VALIDATION_EVENT:
            self.state = self.state.append(
                ChatMessage.system(
                    _validation_event_text(stream_event.payload),
                    kind=ChatEventKind.RUN,
                )
            )
        elif stream_event.event is ConversationStreamEventKind.MODEL_ATTEMPT:
            if self.state.details_enabled:
                self.state = self.state.append(
                    ChatMessage.system(
                        _model_attempt_text(stream_event.payload),
                        kind=ChatEventKind.MODEL,
                    )
                )
        elif stream_event.event is ConversationStreamEventKind.MESSAGE_COMPLETED:
            final_content = stream_event.payload.get("content")
            if isinstance(final_content, str):
                self.state = self.state.upsert_streaming_assistant(final_content)
            self.state = self.state.note_model_metadata(stream_event.payload)
            if "changed_files" in stream_event.payload:
                self.state = self.state.append(
                    ChatMessage.system(
                        render_changed_files(
                            changed_file_summaries_from_payload(
                                stream_event.payload.get("changed_files")
                            )
                        ).plain,
                        kind=ChatEventKind.RUN,
                    )
                )
        elif stream_event.event is ConversationStreamEventKind.ERROR:
            if stream_event.payload.get("approval_required") is True:
                prompt = _approval_prompt_from_payload(stream_event.payload)
                if prompt is not None:
                    self.state = self.state.with_approval_prompt(prompt)
            message = self._format_stream_error(
                stream_event.payload,
                fallback="Conversation failed.",
            )
            self.state = self.state.append(
                ChatMessage.system(str(message), kind=ChatEventKind.ERROR)
            )
        self._render()
        self._focus_prompt()

    def _remember_stream_event(self, stream_event: ConversationStreamEvent) -> bool:
        run_id = _stream_event_run_id(stream_event)
        sequence = stream_event.runtime_sequence
        if run_id is None or sequence is None:
            return True
        key = (run_id, sequence)
        if key in self._seen_runtime_events:
            return False
        self._seen_runtime_events.add(key)
        self._last_runtime_sequence_by_run[run_id] = max(
            sequence,
            self._last_runtime_sequence_by_run.get(run_id, 0),
        )
        return True

    def _last_runtime_sequence(self, run_id: str | None) -> int:
        if run_id is None:
            return 0
        return self._last_runtime_sequence_by_run.get(run_id, 0)

    def _apply_approval_choice(self, index: int) -> None:
        prompt = self.state.pending_approval
        if prompt is None:
            return
        if index == 0:
            self.client.decide_approval(
                prompt.run_id,
                prompt.approval_id,
                approved=True,
                thread_id=self.state.backend_thread_id,
            )
            self.state = self.state.with_approval_prompt(None).append(
                ChatMessage.system(
                    "Approved once. Continuing response.",
                    kind=ChatEventKind.APPROVAL,
                )
            )
            self._start_continue_turn(expected_run_id=prompt.run_id)
        elif index == 1:
            self.client.decide_approval(
                prompt.run_id,
                prompt.approval_id,
                approved=False,
                thread_id=self.state.backend_thread_id,
            )
            self.state = self.state.with_approval_prompt(None).append(
                ChatMessage.system(
                    "Denied. Continuing response with the denied tool result.",
                    kind=ChatEventKind.APPROVAL,
                )
            )
            self._start_continue_turn(expected_run_id=prompt.run_id)
        elif index == 2:
            self.client.cancel(prompt.run_id, thread_id=self.state.backend_thread_id)
            self.state = self.state.with_approval_prompt(None).append(
                ChatMessage.system("Cancelling Run...", kind=ChatEventKind.RUN)
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

    def _record_continue_exception(self, error: Exception) -> None:
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
        self.state = self.state.clear_staged_skills()
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

    def _open_picker(self, parsed: SlashCommand) -> None:
        if parsed.argument:
            self.state = self.state.append(
                ChatMessage.system(
                    (
                        f"/{parsed.kind.value} does not take arguments. "
                        "Use the picker to choose a value."
                    ),
                    kind=ChatEventKind.ERROR,
                )
            )
            return
        if parsed.kind is SlashCommandKind.MODEL:
            models = self.client.list_models()
            items = [
                PickerItem(
                    id=str(item.get("name") or item.get("id") or "model"),
                    label=str(
                        item.get("display_name")
                        or item.get("name")
                        or item.get("id")
                        or "Model"
                    ),
                    disabled=item.get("configured") is False,
                )
                for item in models
            ] or [
                PickerItem(id=self.state.current_model, label=self.state.current_model)
            ]
            self.state = self.state.open_picker(
                PickerState.open(
                    kind="model",
                    title="Select model for this conversation",
                    items=items,
                    selected_id=self.state.current_model,
                )
            )
            return
        if parsed.kind is SlashCommandKind.THINKING:
            self.state = self.state.open_picker(
                PickerState.open(
                    kind="thinking",
                    title="Thinking mode",
                    items=[
                        PickerItem(id="on_high", label="On - high"),
                        PickerItem(id="on_max", label="On - max"),
                        PickerItem(id="off", label="Off"),
                    ],
                    selected_id=self.state.thinking_mode,
                )
            )
            return
        if parsed.kind is SlashCommandKind.MEMORY:
            self.state = self.state.open_picker(
                PickerState.open(
                    kind="memory_root",
                    title="Memory",
                    items=[
                        PickerItem(id="local", label="Local memory"),
                        PickerItem(id="provider", label="Provider memory"),
                    ],
                )
            )
            return
        if parsed.kind is SlashCommandKind.THREADS:
            threads = self.client.list_threads()
            items = [
                PickerItem(
                    id=_thread_id(thread),
                    label=_thread_title(thread),
                    description=_thread_picker_description(thread),
                )
                for thread in threads
            ] or [PickerItem(id="none", label="No conversations yet", disabled=True)]
            self.state = self.state.open_picker(
                PickerState.open(
                    kind="threads",
                    title="Conversations",
                    items=items,
                    selected_id=self.state.backend_thread_id,
                )
            )
            return
        if parsed.kind is SlashCommandKind.SKILLS:
            skills = self.client.list_skills()
            items = [
                PickerItem(
                    id=str(item.get("id") or item.get("name")),
                    label=str(item.get("name") or item.get("id")),
                )
                for item in skills
                if item.get("id") or item.get("name")
            ] or [PickerItem(id="none", label="No skills available", disabled=True)]
            self.state = self.state.open_picker(
                PickerState.open(
                    kind="skills",
                    title="Skills for next turn",
                    items=items,
                )
            )

    def _apply_picker(self) -> None:
        picker = self.state.active_picker
        if picker is None:
            return
        item = picker.apply()
        if item is None:
            self.state = self.state.close_picker()
            self._render()
            return
        if picker.kind == "model":
            self.state = self.state.with_model(item.id).close_picker()
            self._persist_thread_settings(default_model=item.id)
            self.state = self.state.append(
                ChatMessage.system(
                    f"Model changed to: {item.label}\nApplies to this conversation."
                )
            )
        elif picker.kind == "thinking":
            self.state = self.state.with_thinking(item.id).close_picker()
            self._persist_thread_settings(thinking_mode=item.id)
            self.state = self.state.append(
                ChatMessage.system(
                    f"Thinking changed to: {item.label}\nApplies to this conversation."
                )
            )
        elif picker.kind == "memory_root":
            self._open_memory_picker(item.id)
        elif picker.kind == "memory_local":
            self._apply_local_memory_picker(item)
        elif picker.kind == "memory_provider":
            provider = None if item.id == "disabled" else item.id
            self.state = self.state.with_provider_memory(provider).close_picker()
            self._persist_thread_settings(provider_memory=provider)
            self.state = self.state.append(
                ChatMessage.system(f"Provider memory changed to: {item.label}")
            )
        elif picker.kind == "threads":
            self._restore_thread(item.id)
        elif picker.kind == "skills":
            if item.id in self.state.staged_skill_ids:
                self.state = self.state.unstage_skill(item.id).close_picker()
                self.state = self.state.append(
                    ChatMessage.system(f"Skill removed from next turn: {item.label}")
                )
            else:
                self.state = self.state.stage_skill(item.id).close_picker()
                self.state = self.state.append(
                    ChatMessage.system(
                        f"Skill staged for next turn: {item.label}\n"
                        "It will be cleared after the next response."
                    )
                )
        self._render()
        self._focus_prompt()

    def _open_memory_picker(self, item_id: str) -> None:
        if item_id == "local":
            self.state = self.state.open_picker(
                PickerState.open(
                    kind="memory_local",
                    title="Local memory",
                    items=[
                        PickerItem(id="enabled", label="Enabled"),
                        PickerItem(id="disabled", label="Disabled"),
                        PickerItem(id="view", label="View remembered facts"),
                    ],
                    selected_id=(
                        "enabled" if self.state.local_memory_enabled else "disabled"
                    ),
                )
            )
            return
        self.state = self.state.open_picker(
            PickerState.open(
                kind="memory_provider",
                title="Provider memory",
                items=[
                    PickerItem(id="disabled", label="Disabled"),
                    PickerItem(id="mem0", label="Mem0"),
                ],
                selected_id=self.state.provider_memory or "disabled",
            )
        )

    def _apply_local_memory_picker(self, item: PickerItem) -> None:
        if item.id == "view":
            facts = self._local_memory_facts()
            content = "Remembered facts"
            if facts:
                content = "\n".join([content, "", *[f"- {fact}" for fact in facts]])
            else:
                content = "\n".join(
                    [
                        content,
                        "",
                        "No local memory facts for this conversation.",
                    ]
                )
            self.state = self.state.close_picker().append(ChatMessage.system(content))
            return
        enabled = item.id == "enabled"
        self.state = self.state.with_local_memory(enabled).close_picker()
        self._persist_thread_settings(local_memory_enabled=enabled)
        self.state = self.state.append(
            ChatMessage.system(
                f"Local memory changed to: {'Enabled' if enabled else 'Disabled'}"
            )
        )

    def _local_memory_facts(self) -> list[str]:
        facts = getattr(self.client, "local_memory_facts", None)
        if not callable(facts):
            return []
        try:
            return [str(item) for item in facts(self.state.backend_thread_id)]
        except Exception:
            return []

    def _restore_thread(self, thread_id: str) -> None:
        thread = self.client.resume_thread(thread_id)
        messages = chat_messages_from_thread_records(
            self.client.list_thread_messages(_thread_id(thread))
        )
        self.state = self.state.switch_thread(
            backend_thread_id=_thread_id(thread),
            title=_thread_title(thread),
            context_label=_thread_context_label(thread),
            messages=messages,
        )
        self.state = _apply_thread_settings(self.state, thread)
        self.state = self.state.close_picker().append(
            ChatMessage.system(
                f"Opened conversation: {_thread_title(thread)}",
                kind=ChatEventKind.RUN,
            )
        )
        self._render()
        self._focus_prompt()

    def _persist_thread_settings(
        self,
        *,
        default_model: str | None = None,
        thinking_mode: str | None = None,
        local_memory_enabled: bool | None = None,
        provider_memory: str | None = None,
    ) -> None:
        if self.state.backend_thread_id is None:
            return
        update = getattr(self.client, "update_thread_settings", None)
        if not callable(update):
            return
        effective_model = default_model or self.state.current_model
        effective_thinking = thinking_mode or self.state.thinking_mode
        effective_local_memory = (
            local_memory_enabled
            if local_memory_enabled is not None
            else self.state.local_memory_enabled
        )
        effective_provider_memory = (
            provider_memory
            if provider_memory is not None
            else self.state.provider_memory
        )
        with suppress(Exception):
            update(
                self.state.backend_thread_id,
                default_model=effective_model,
                thinking_mode=effective_thinking,
                local_memory_enabled=effective_local_memory,
                provider_memory=effective_provider_memory,
            )

    def _command_worker(
        self,
        parsed: SlashCommand,
        state: ChatSessionState,
    ) -> None:
        failed = False
        try:
            if parsed.kind is SlashCommandKind.NEW:
                thread, message = self._create_thread(parsed, state)
                self.call_from_thread(
                    self._switch_to_thread,
                    thread,
                    [],
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
            default_model=self.state.current_model,
            thinking_mode=self.state.thinking_mode,
            local_memory_enabled=self.state.local_memory_enabled,
            provider_memory=self.state.provider_memory,
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
            default_model=state.current_model,
            thinking_mode=state.thinking_mode,
            local_memory_enabled=state.local_memory_enabled,
            provider_memory=state.provider_memory,
        )
        return (
            thread,
            ChatMessage.system(
                f"New conversation started: {_thread_title(thread)}",
                kind=ChatEventKind.RUN,
            ),
        )

    def _focus_prompt(self) -> None:
        with suppress(NoMatches):
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


def _thread_picker_description(thread: SurfaceThread | dict[str, object]) -> str:
    if isinstance(thread, SurfaceThread):
        updated = thread.updated_label or "-"
        changes = _changed_file_label(thread.changed_file_count)
        return f"{updated} - {changes}"
    updated = str(thread.get("updated_label") or "-")
    count = thread.get("changed_file_count")
    return f"{updated} - {_changed_file_label(count if isinstance(count, int) else 0)}"


def _changed_file_label(count: int) -> str:
    if count == 0:
        return "no file changes"
    if count == 1:
        return "1 changed file"
    return f"{count} changed files"


def _stream_event_run_id(event: ConversationStreamEvent) -> str | None:
    if event.run_id is not None:
        return str(event.run_id)
    run_id = event.payload.get("run_id")
    if isinstance(run_id, UUID):
        return str(run_id)
    if isinstance(run_id, str) and run_id:
        return run_id
    return None


def _apply_thread_settings(
    state: ChatSessionState,
    thread: SurfaceThread | dict[str, object],
) -> ChatSessionState:
    if isinstance(thread, SurfaceThread):
        if thread.default_model:
            state = state.with_model(thread.default_model)
        if thread.thinking_mode:
            state = state.with_thinking(thread.thinking_mode)
        state = state.with_local_memory(thread.local_memory_enabled)
        state = state.with_provider_memory(thread.provider_memory)
        return state
    default_model = thread.get("default_model")
    thinking_mode = thread.get("thinking_mode")
    provider_memory = thread.get("provider_memory")
    if isinstance(default_model, str) and default_model:
        state = state.with_model(default_model)
    if isinstance(thinking_mode, str) and thinking_mode:
        state = state.with_thinking(thinking_mode)
    state = state.with_local_memory(thread.get("local_memory_enabled") is True)
    state = state.with_provider_memory(
        provider_memory if isinstance(provider_memory, str) else None
    )
    return state


def _tool_display_event(payload: dict[str, object]) -> ToolDisplayEvent:
    name = str(payload.get("name") or payload.get("tool") or "tool")
    summary = str(
        payload.get("summary")
        or payload.get("result")
        or payload.get("status")
        or "started"
    )
    details = {
        str(key): value
        for key, value in payload.items()
        if key not in {"name", "tool", "summary", "result", "status"}
    }
    return ToolDisplayEvent(name=name, summary=summary, details=details)


def _team_display_event(payload: dict[str, object]) -> TeamDisplayEvent:
    role = str(
        payload.get("role") or payload.get("agent") or payload.get("kind") or "Team"
    )
    summary = str(
        payload.get("summary")
        or payload.get("task")
        or payload.get("status")
        or "activity"
    )
    details = {
        str(key): value
        for key, value in payload.items()
        if key not in {"role", "agent", "kind", "summary", "task", "status"}
    }
    title = "Review" if "verifier" in role.casefold() else "Team"
    return TeamDisplayEvent(title=title, summary=f"{role}: {summary}", details=details)


def _validation_event_text(payload: dict[str, object]) -> str:
    status = str(payload.get("status") or payload.get("result") or "validation")
    summary = payload.get("summary") or payload.get("message") or payload.get("reason")
    if isinstance(summary, str) and summary:
        return f"Validation - {status}: {summary}"
    return f"Validation - {status}"


def _model_attempt_text(payload: dict[str, object]) -> str:
    provider = str(payload.get("provider") or "provider")
    model = str(payload.get("model") or "model")
    outcome = str(payload.get("outcome") or payload.get("status") or "attempt")
    reason = payload.get("fallback_reason") or payload.get("error_code")
    if isinstance(reason, str) and reason:
        return f"Model attempt - {provider}/{model}: {outcome} ({reason})"
    return f"Model attempt - {provider}/{model}: {outcome}"


def _approval_prompt_from_payload(
    payload: dict[str, object],
) -> ApprovalPromptState | None:
    run_id = payload.get("run_id")
    approval_id = payload.get("approval_id")
    if not isinstance(run_id, str) or not isinstance(approval_id, str):
        return None
    approval_type = str(payload.get("approval_type") or "edit")
    subject = str(
        payload.get("path")
        or payload.get("command")
        or payload.get("tool")
        or payload.get("message")
        or "requested action"
    )
    if approval_type == "command":
        title = "Leader wants to run:"
    else:
        title = "Leader wants to create:"
    return ApprovalPromptState(
        run_id=run_id,
        approval_id=approval_id,
        title=title,
        subject=subject,
        approval_type=approval_type,
    )
