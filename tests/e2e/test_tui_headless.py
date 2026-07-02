from collections.abc import Iterable
from pathlib import Path
from time import sleep
from uuid import uuid4

import pytest
from textual.widgets import Input

from awesome_agent.cli.config_flow import ConfigFlowSummary
from awesome_agent.cli.repo_context import CliLaunchContext
from awesome_agent.conversation.events import (
    ConversationStreamEvent,
    ConversationStreamEventKind,
)
from awesome_agent.modeling.messages import AssistantMessage
from awesome_agent.modeling.provider import StructuredModelProvider
from awesome_agent.modeling.stream import ModelStreamEvent, TextDelta, TurnCompleted
from awesome_agent.modeling.turns import ModelRequest, ModelTurn, StopReason
from awesome_agent.surfaces.local_client import LocalSurfaceClient
from awesome_agent.surfaces.local_runtime_host import LocalRuntimeHost
from awesome_agent.tui.app import AwesomeAgentTui


class FakeClient:
    def __init__(self) -> None:
        self.thread_id = str(uuid4())
        self.turns: list[tuple[str, str]] = []
        self.runs: list[dict[str, object]] = []
        self.cancelled_runs: list[str] = []
        self.created_threads = 0
        self.threads: list[dict[str, object]] = []
        self.messages_by_thread: dict[str, list[dict[str, object]]] = {}
        self.resumed_queries: list[str] = []

    def create_thread(
        self,
        title: str,
        *,
        context_kind: str | None = None,
        context_path: str | None = None,
        repository_id: str | None = None,
        default_model: str | None = None,
        sandbox_profile: str | None = None,
    ) -> dict[str, object]:
        thread_id = self.thread_id if self.created_threads == 0 else str(uuid4())
        self.created_threads += 1
        thread = {
            "id": thread_id,
            "title": title,
            "context_kind": context_kind or "workspace",
            "context_path": context_path,
            "repository_id": repository_id,
            "default_model": default_model,
            "sandbox_profile": sandbox_profile,
            "logical_workspace_path": "/mnt/user-data/workspace/",
            "updated_label": "now",
        }
        self.threads.insert(0, thread)
        self.messages_by_thread.setdefault(thread_id, [])
        return thread

    def list_threads(self) -> list[dict[str, object]]:
        return list(self.threads)

    def resume_thread(self, query: str) -> dict[str, object]:
        self.resumed_queries.append(query)
        normalized = query.casefold()
        for thread in self.threads:
            thread_id = str(thread["id"])
            title = str(thread["title"])
            if (
                thread_id == query
                or thread_id.startswith(query)
                or normalized in title.casefold()
            ):
                return thread
        raise ValueError(f"Thread not found: {query}")

    def list_thread_messages(self, thread_id: str) -> list[dict[str, object]]:
        return list(self.messages_by_thread.get(thread_id, []))

    def stream_turn(
        self,
        thread_id: str,
        content: str,
        *,
        model: str | None = None,
        resume_run_id: str | None = None,
    ) -> list[ConversationStreamEvent]:
        self.turns.append((thread_id, content))
        self.messages_by_thread.setdefault(thread_id, []).append(
            {"role": "user", "content": content, "kind": "message"}
        )
        self.messages_by_thread[thread_id].append(
            {"role": "assistant", "content": "hello world", "kind": "model"}
        )
        turn_id = uuid4()
        trace_id = "trace-test"
        return [
            ConversationStreamEvent(
                event=ConversationStreamEventKind.MESSAGE_DELTA,
                thread_id=uuid4(),
                turn_id=turn_id,
                sequence=1,
                trace_id=trace_id,
                payload={"text": "hello"},
            ),
            ConversationStreamEvent(
                event=ConversationStreamEventKind.MESSAGE_DELTA,
                thread_id=uuid4(),
                turn_id=turn_id,
                sequence=2,
                trace_id=trace_id,
                payload={"text": " world"},
            ),
            ConversationStreamEvent(
                event=ConversationStreamEventKind.MESSAGE_COMPLETED,
                thread_id=uuid4(),
                turn_id=turn_id,
                sequence=3,
                trace_id=trace_id,
                payload={"content": "hello world"},
            ),
        ]

    def create_thread_run(
        self,
        thread_id: str,
        goal: str,
        *,
        intent: str = "modifying",
        mode: str = "solo",
        repository_id: str | None = None,
        repository_path: str | None = None,
    ) -> dict[str, object]:
        run_id = str(uuid4())
        payload = {
            "id": run_id,
            "thread_id": thread_id,
            "goal": goal,
            "intent": intent,
            "mode": mode,
            "repository_id": repository_id,
            "repository_path": repository_path,
            "status": "created",
        }
        self.runs.append(payload)
        return payload

    def runtime_status(self) -> dict[str, object]:
        return {"api": "ready", "sandbox": "local"}

    def list_models(self) -> list[dict[str, object]]:
        return [{"name": "deepseek-v4-pro", "role": "leader"}]

    def memory_summary(self) -> dict[str, object]:
        return {"enabled": False}

    def cancel(self, run_id: str) -> dict[str, object]:
        self.cancelled_runs.append(run_id)
        return {"id": run_id, "status": "cancelled"}


class SlowStatusClient(FakeClient):
    def __init__(self, *, delay_seconds: float) -> None:
        super().__init__()
        self.delay_seconds = delay_seconds

    def runtime_status(self) -> dict[str, object]:
        sleep(self.delay_seconds)
        return super().runtime_status()


class SlowStreamingClient(FakeClient):
    def __init__(
        self,
        deltas: list[str],
        *,
        run_id: str | None = None,
        delay_seconds: float = 0.05,
    ) -> None:
        super().__init__()
        self.deltas = deltas
        self.run_id = run_id
        self.delay_seconds = delay_seconds
        self.resume_run_ids: list[str | None] = []

    def stream_turn(
        self,
        thread_id: str,
        content: str,
        *,
        model: str | None = None,
        resume_run_id: str | None = None,
    ) -> Iterable[ConversationStreamEvent]:
        self.turns.append((thread_id, content))
        self.resume_run_ids.append(resume_run_id)
        turn_id = uuid4()
        for sequence, delta in enumerate(self.deltas, start=1):
            sleep(self.delay_seconds)
            payload: dict[str, object] = {"text": delta}
            if self.run_id is not None and sequence == 1:
                payload["run_id"] = self.run_id
            yield ConversationStreamEvent(
                event=ConversationStreamEventKind.MESSAGE_DELTA,
                thread_id=uuid4(),
                turn_id=turn_id,
                sequence=sequence,
                trace_id="trace-slow",
                payload=payload,
            )
        yield ConversationStreamEvent(
            event=ConversationStreamEventKind.MESSAGE_COMPLETED,
            thread_id=uuid4(),
            turn_id=turn_id,
            sequence=len(self.deltas) + 1,
            trace_id="trace-slow",
            payload={"content": "".join(self.deltas)},
        )


class ReasoningStreamingClient(FakeClient):
    def stream_turn(
        self,
        thread_id: str,
        content: str,
        *,
        model: str | None = None,
        resume_run_id: str | None = None,
    ) -> Iterable[ConversationStreamEvent]:
        self.turns.append((thread_id, content))
        turn_id = uuid4()
        events = [
            (
                ConversationStreamEventKind.REASONING_STARTED,
                {},
            ),
            (
                ConversationStreamEventKind.REASONING_DELTA,
                {"text": "inspect context. "},
            ),
            (
                ConversationStreamEventKind.REASONING_DELTA,
                {"text": "choose answer."},
            ),
            (
                ConversationStreamEventKind.MESSAGE_DELTA,
                {"text": "final answer"},
            ),
            (
                ConversationStreamEventKind.REASONING_COMPLETED,
                {},
            ),
            (
                ConversationStreamEventKind.MESSAGE_COMPLETED,
                {"content": "final answer"},
            ),
        ]
        for sequence, (event, payload) in enumerate(events, start=1):
            yield ConversationStreamEvent(
                event=event,
                thread_id=uuid4(),
                turn_id=turn_id,
                sequence=sequence,
                trace_id="trace-reasoning",
                payload=payload,
            )


class FakeProvider(StructuredModelProvider):
    async def stream(self, request: ModelRequest) -> ModelStreamEvent:
        yield TextDelta(text="embedded")
        yield TurnCompleted(
            turn=ModelTurn(
                assistant=AssistantMessage(content="embedded"),
                stop_reason=StopReason.COMPLETED,
                model="fake-model",
                provider="fake",
            )
        )


@pytest.mark.asyncio
async def test_tui_headless_renders_help() -> None:
    app = AwesomeAgentTui(api_url="http://127.0.0.1:8000", client=FakeClient())

    async with app.run_test() as pilot:
        await pilot.click("#prompt")
        await pilot.press("/", "h", "e", "l", "p", "enter")
        transcript = app.query_one("#transcript").render()

    assert "/new" in str(transcript)


@pytest.mark.asyncio
async def test_tui_slash_opens_command_suggestions() -> None:
    app = AwesomeAgentTui(api_url="http://127.0.0.1:8000", client=FakeClient())

    async with app.run_test() as pilot:
        await pilot.click("#prompt")
        await pilot.press("/")
        palette = app.query_one("#command-palette").render()

    rendered = str(palette)
    assert "/new" in rendered
    assert "/status" in rendered


@pytest.mark.asyncio
async def test_tui_slash_prefix_filters_command_suggestions() -> None:
    app = AwesomeAgentTui(api_url="http://127.0.0.1:8000", client=FakeClient())

    async with app.run_test() as pilot:
        await pilot.click("#prompt")
        await pilot.press("/", "s")
        palette = app.query_one("#command-palette").render()

    rendered = str(palette)
    assert "/status" in rendered
    assert "/skills" in rendered
    assert "/new" not in rendered


@pytest.mark.asyncio
async def test_tui_tab_completes_active_command() -> None:
    app = AwesomeAgentTui(api_url="http://127.0.0.1:8000", client=FakeClient())

    async with app.run_test() as pilot:
        await pilot.click("#prompt")
        await pilot.press("/", "s", "tab")
        prompt = app.query_one("#prompt", Input)
        palette = app.query_one("#command-palette").render()

    assert prompt.value == "/status "
    assert str(palette) == ""


@pytest.mark.asyncio
async def test_tui_enter_executes_active_prefix_candidate() -> None:
    app = AwesomeAgentTui(api_url="http://127.0.0.1:8000", client=FakeClient())

    async with app.run_test() as pilot:
        await pilot.click("#prompt")
        await pilot.press("/", "s", "enter")
        transcript = app.query_one("#transcript").render()

    assert "api=ready" in str(transcript)


@pytest.mark.asyncio
async def test_tui_status_does_not_block_input_focus() -> None:
    app = AwesomeAgentTui(client=SlowStatusClient(delay_seconds=0.2))

    async with app.run_test() as pilot:
        await pilot.click("#prompt")
        await pilot.press("/", "s", "t", "a", "t", "u", "s", "enter")
        prompt = app.query_one("#prompt", Input)
        assert prompt.has_focus
        await pilot.press("h", "i")

    assert prompt.value == "hi"


@pytest.mark.asyncio
async def test_tui_streams_first_delta_before_completion() -> None:
    app = AwesomeAgentTui(
        client=SlowStreamingClient(["hello", " world"], run_id="run-1")
    )

    async with app.run_test() as pilot:
        await pilot.click("#prompt")
        await pilot.press("h", "i", "enter")
        await pilot.pause(0.1)
        transcript = app.query_one("#transcript").render()

    assert "hello" in str(transcript)
    assert app.state.current_run_id == "run-1"


@pytest.mark.asyncio
async def test_ctrl_c_pauses_active_stream_and_keeps_prompt() -> None:
    client = SlowStreamingClient(
        ["hello", " world"],
        run_id="run-1",
        delay_seconds=0.5,
    )
    app = AwesomeAgentTui(client=client)

    async with app.run_test() as pilot:
        await pilot.click("#prompt")
        await pilot.press("h", "i", "enter")
        await pilot.pause(0.65)
        await pilot.press("ctrl+c")
        await pilot.pause()
        transcript = app.query_one("#transcript").render()
        prompt = app.query_one("#prompt", Input)
        await pilot.press("x")

    rendered = str(transcript)
    assert "hello" in rendered
    assert "Response paused" in rendered
    assert app.state.last_resumable_run_id is not None
    assert prompt.value == "x"


@pytest.mark.asyncio
async def test_continue_resumes_last_resumable_run() -> None:
    client = SlowStreamingClient(["continued"], run_id="run-1")
    app = AwesomeAgentTui(client=client)
    app.state = app.state.mark_operation_paused("run-1")

    async with app.run_test() as pilot:
        await pilot.click("#prompt")
        await pilot.press("c", "o", "n", "t", "i", "n", "u", "e", "enter")
        await pilot.pause(0.1)

    assert client.resume_run_ids == ["run-1"]


@pytest.mark.asyncio
async def test_tui_reasoning_thought_collapses_and_toggles() -> None:
    app = AwesomeAgentTui(client=ReasoningStreamingClient())

    async with app.run_test() as pilot:
        await pilot.click("#prompt")
        await pilot.press("h", "i", "enter")
        await pilot.pause()
        collapsed = str(app.query_one("#transcript").render())
        await pilot.press("ctrl+o")
        expanded = str(app.query_one("#transcript").render())
        await pilot.press("ctrl+o")
        collapsed_again = str(app.query_one("#transcript").render())

    assert "Thought for " in collapsed
    assert "ctrl+o to expand" in collapsed
    assert "inspect context" not in collapsed
    assert "final answer" in collapsed
    assert "inspect context. choose answer." in expanded
    assert "ctrl+o to collapse" in expanded
    assert "inspect context" not in collapsed_again


@pytest.mark.asyncio
async def test_tui_escape_closes_command_suggestions() -> None:
    app = AwesomeAgentTui(api_url="http://127.0.0.1:8000", client=FakeClient())

    async with app.run_test() as pilot:
        await pilot.click("#prompt")
        await pilot.press("/")
        assert "/new" in str(app.query_one("#command-palette").render())
        await pilot.press("escape")
        palette = app.query_one("#command-palette").render()

    assert str(palette) == ""


@pytest.mark.asyncio
async def test_tui_help_keeps_prompt_focused_and_allows_next_message() -> None:
    app = AwesomeAgentTui(api_url="http://127.0.0.1:8000", client=FakeClient())

    async with app.run_test() as pilot:
        await pilot.click("#prompt")
        await pilot.press("/", "h", "e", "l", "p", "enter")
        prompt = app.query_one("#prompt", Input)
        assert prompt.has_focus
        await pilot.press("h", "i", "enter")
        transcript = app.query_one("#transcript").render()

    rendered = str(transcript)
    assert "/new" in rendered
    assert "hello world" in rendered


@pytest.mark.asyncio
async def test_tui_accepts_plain_message_without_repo_selection_block(
    tmp_path: Path,
) -> None:
    client = FakeClient()
    app = AwesomeAgentTui(
        api_url="http://127.0.0.1:8000",
        client=client,
        launch_context=CliLaunchContext(
            project_root=tmp_path,
            context_kind="workspace",
        ),
    )

    async with app.run_test() as pilot:
        await pilot.click("#prompt")
        await pilot.press("h", "i", "enter")
        transcript = app.query_one("#transcript").render()

    rendered = str(transcript)
    assert "hi" in rendered
    assert "hello world" in rendered
    assert client.turns == [(client.thread_id, "hi")]
    assert "Select repository context" not in rendered


@pytest.mark.asyncio
async def test_first_plain_message_creates_thread_automatically() -> None:
    client = FakeClient()
    app = AwesomeAgentTui(client=client)

    async with app.run_test() as pilot:
        await pilot.click("#prompt")
        await pilot.press("h", "i", "enter")

    assert client.created_threads == 1
    assert app.state.backend_thread_id == client.thread_id


@pytest.mark.asyncio
async def test_tui_new_switches_thread_without_internal_path_leak(
    tmp_path: Path,
) -> None:
    client = FakeClient()
    app = AwesomeAgentTui(
        client=client,
        launch_context=CliLaunchContext(
            project_root=tmp_path,
            context_kind="workspace",
        ),
    )

    async with app.run_test() as pilot:
        await pilot.click("#prompt")
        await pilot.press("h", "i", "enter")
        old_thread_id = app.state.backend_thread_id
        await pilot.press("/", "n", "e", "w", " ", "f", "r", "e", "s", "h", "enter")
        await pilot.pause()
        transcript = app.query_one("#transcript").render()

    rendered = str(transcript)
    assert app.state.backend_thread_id != old_thread_id
    assert app.state.thread_title == "fresh"
    assert "Started thread" not in rendered
    assert "/mnt/user-data/workspace" not in rendered
    assert "New conversation started: fresh" in rendered
    assert "hello world" not in rendered


@pytest.mark.asyncio
async def test_tui_resume_switches_thread_and_loads_messages() -> None:
    client = FakeClient()
    original = client.create_thread("Original")
    restored = client.create_thread("Restored")
    restored_id = str(restored["id"])
    client.messages_by_thread[restored_id] = [
        {"role": "user", "content": "old question", "kind": "message"},
        {"role": "assistant", "content": "old answer", "kind": "model"},
    ]
    app = AwesomeAgentTui(client=client)
    app.state = app.state.switch_thread(
        backend_thread_id=str(original["id"]),
        title="Original",
        context_label=None,
    )

    async with app.run_test() as pilot:
        await pilot.click("#prompt")
        await pilot.press(
            "/",
            "r",
            "e",
            "s",
            "u",
            "m",
            "e",
            " ",
            "r",
            "e",
            "s",
            "t",
            "o",
            "r",
            "e",
            "d",
            "enter",
        )
        await pilot.pause()
        transcript = app.query_one("#transcript").render()

    rendered = str(transcript)
    assert app.state.backend_thread_id == restored_id
    assert client.resumed_queries == ["restored"]
    assert "old question" in rendered
    assert "old answer" in rendered
    assert "Resumed conversation: Restored" in rendered


@pytest.mark.asyncio
async def test_tui_threads_lists_current_thread_without_internal_paths() -> None:
    client = FakeClient()
    current = client.create_thread(
        "Current",
        context_path="/mnt/user-data/workspace/",
    )
    client.create_thread("Other", context_path="E:\\other")
    app = AwesomeAgentTui(client=client)
    app.state = app.state.switch_thread(
        backend_thread_id=str(current["id"]),
        title="Current",
        context_label=str(current["context_path"]),
    )

    async with app.run_test() as pilot:
        await pilot.click("#prompt")
        await pilot.press("/", "t", "h", "r", "e", "a", "d", "s", "enter")
        await pilot.pause()
        transcript = app.query_one("#transcript").render()

    rendered = str(transcript)
    assert "Threads" in rendered
    assert "* Current" in rendered
    assert "Other" in rendered
    assert "/mnt/user-data/workspace" not in rendered


@pytest.mark.asyncio
async def test_tui_can_answer_without_http_server() -> None:
    client = FakeClient()
    app = AwesomeAgentTui(client=client)

    async with app.run_test() as pilot:
        await pilot.click("#prompt")
        await pilot.press("h", "i", "enter")
        transcript = app.query_one("#transcript").render()

    assert "hello world" in str(transcript)
    assert client.turns == [(client.thread_id, "hi")]


@pytest.mark.asyncio
async def test_tui_can_answer_with_real_local_surface_client() -> None:
    host = LocalRuntimeHost(
        provider_factory=lambda _model: FakeProvider(),
        default_model="fake-model",
    )
    app = AwesomeAgentTui(client=LocalSurfaceClient(host=host))

    async with app.run_test() as pilot:
        await pilot.click("#prompt")
        await pilot.press("h", "i", "enter")
        transcript = app.query_one("#transcript").render()

    assert "embedded" in str(transcript)


@pytest.mark.asyncio
async def test_tui_status_includes_launch_context(tmp_path: Path) -> None:
    app = AwesomeAgentTui(
        api_url="http://127.0.0.1:8000",
        client=FakeClient(),
        launch_context=CliLaunchContext(
            project_root=tmp_path,
            context_kind="workspace",
        ),
    )

    async with app.run_test() as pilot:
        await pilot.click("#prompt")
        await pilot.press("/", "s", "t", "a", "t", "u", "s", "enter")
        transcript = app.query_one("#transcript").render()

    assert f"workspace={tmp_path}" in str(transcript)


@pytest.mark.asyncio
async def test_tui_run_uses_current_repo_context(tmp_path: Path) -> None:
    client = FakeClient()
    app = AwesomeAgentTui(
        api_url="http://127.0.0.1:8000",
        client=client,
        launch_context=CliLaunchContext(
            project_root=tmp_path / "nested",
            context_kind="repo",
            git_root=tmp_path,
        ),
    )

    async with app.run_test() as pilot:
        await pilot.click("#prompt")
        await pilot.press("/", "r", "u", "n", " ", "b", "u", "i", "l", "d", "enter")
        transcript = app.query_one("#transcript").render()

    assert "Started Coding Run" in str(transcript)
    assert client.runs == [
        {
            "id": app.state.current_run_id,
            "thread_id": client.thread_id,
            "goal": "build",
            "intent": "modifying",
            "mode": "solo",
            "repository_id": None,
            "repository_path": str(tmp_path),
            "status": "created",
        }
    ]


@pytest.mark.asyncio
async def test_tui_renders_minimal_welcome_card(tmp_path: Path) -> None:
    app = AwesomeAgentTui(
        api_url="http://127.0.0.1:8000",
        client=FakeClient(),
        launch_context=CliLaunchContext(
            project_root=tmp_path,
            context_kind="workspace",
        ),
    )

    async with app.run_test():
        welcome = app.query_one("#welcome").render()
        footer = app.query_one("#shortcuts").render()

    assert "Awesome Agent" in str(welcome)
    assert str(tmp_path) in str(welcome)
    assert "? for shortcuts" in str(footer)


@pytest.mark.asyncio
async def test_tui_hides_welcome_after_first_message() -> None:
    app = AwesomeAgentTui(client=FakeClient())

    async with app.run_test() as pilot:
        await pilot.click("#prompt")
        await pilot.press("h", "i", "enter")
        welcome = app.query_one("#welcome").render()

    assert str(welcome) == ""


@pytest.mark.asyncio
async def test_tui_details_toggles_verbose_state() -> None:
    app = AwesomeAgentTui(api_url="http://127.0.0.1:8000", client=FakeClient())

    async with app.run_test() as pilot:
        await pilot.click("#prompt")
        await pilot.press("/", "d", "e", "t", "a", "i", "l", "s", "enter")
        transcript = app.query_one("#transcript").render()

    assert app.state.details_enabled is True
    assert "Details enabled" in str(transcript)


@pytest.mark.asyncio
async def test_tui_retry_resends_last_failed_message() -> None:
    class FailingOnceClient(FakeClient):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        def stream_turn(
            self,
            thread_id: str,
            content: str,
            *,
            model: str | None = None,
            resume_run_id: str | None = None,
        ) -> list[ConversationStreamEvent]:
            self.calls += 1
            if self.calls == 1:
                self.turns.append((thread_id, content))
                raise RuntimeError("temporary model failure")
            return super().stream_turn(thread_id, content, model=model)

    client = FailingOnceClient()
    app = AwesomeAgentTui(api_url="http://127.0.0.1:8000", client=client)

    async with app.run_test() as pilot:
        await pilot.click("#prompt")
        await pilot.press("h", "i", "enter")
        assert app.state.last_failed_user_message == "hi"
        await pilot.press("ctrl+r")
        transcript = app.query_one("#transcript").render()

    assert client.turns == [(client.thread_id, "hi"), (client.thread_id, "hi")]
    assert app.state.last_failed_user_message is None
    assert "temporary model failure" in str(transcript)
    assert "hello world" in str(transcript)


@pytest.mark.asyncio
async def test_tui_cancel_current_run_calls_api() -> None:
    client = FakeClient()
    app = AwesomeAgentTui(api_url="http://127.0.0.1:8000", client=client)

    async with app.run_test() as pilot:
        await pilot.click("#prompt")
        await pilot.press("/", "r", "u", "n", " ", "b", "u", "i", "l", "d", "enter")
        assert app.state.current_run_id is not None
        await pilot.press("ctrl+c")
        transcript = app.query_one("#transcript").render()

    assert client.cancelled_runs == [app.state.current_run_id]
    assert "Cancelled Run" in str(transcript)


@pytest.mark.asyncio
async def test_tui_renders_approval_required_stream_error_as_actionable() -> None:
    class ApprovalClient(FakeClient):
        def stream_turn(
            self,
            thread_id: str,
            content: str,
            *,
            model: str | None = None,
            resume_run_id: str | None = None,
        ) -> list[ConversationStreamEvent]:
            self.turns.append((thread_id, content))
            return [
                ConversationStreamEvent(
                    event=ConversationStreamEventKind.ERROR,
                    thread_id=uuid4(),
                    turn_id=uuid4(),
                    sequence=1,
                    trace_id="trace-approval",
                    payload={
                        "code": "approval_required",
                        "message": "Tool approval is required.",
                        "hint": "Use the approvals view to decide.",
                        "approval_required": True,
                    },
                )
            ]

    app = AwesomeAgentTui(api_url="http://127.0.0.1:8000", client=ApprovalClient())

    async with app.run_test() as pilot:
        await pilot.click("#prompt")
        await pilot.press("h", "i", "enter")
        transcript = app.query_one("#transcript").render()

    rendered = str(transcript)
    assert "Action required" in rendered
    assert "Tool approval is required" in rendered
    assert "Use the approvals view to decide" in rendered


@pytest.mark.asyncio
async def test_tui_welcome_shows_first_run_model_guidance(tmp_path: Path) -> None:
    summary = ConfigFlowSummary(
        home=tmp_path,
        project_root=tmp_path / "project",
        user_config=tmp_path / ".awesome-agent" / "config.yaml",
        project_config=tmp_path / "project" / "awesome-agent.yaml",
        project_env=tmp_path / "project" / ".env",
        user_config_exists=False,
        project_config_exists=False,
        project_env_exists=False,
        model_name="deepseek-v4-pro",
        model_api_key_env="AWESOME_AGENT_DEEPSEEK_API_KEY",
        model_api_key_configured=False,
    )
    app = AwesomeAgentTui(
        api_url="http://127.0.0.1:8000",
        client=FakeClient(),
        first_run_summary=summary,
    )

    async with app.run_test():
        welcome = app.query_one("#welcome").render()

    assert "awesome init" in str(welcome)
    assert "AWESOME_AGENT_DEEPSEEK_API_KEY" in str(welcome)
