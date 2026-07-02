from pathlib import Path
from uuid import uuid4

import pytest
from textual.widgets import Input

from awesome_agent.cli.config_flow import ConfigFlowSummary
from awesome_agent.cli.repo_context import CliLaunchContext
from awesome_agent.conversation.events import (
    ConversationStreamEvent,
    ConversationStreamEventKind,
)
from awesome_agent.tui.app import AwesomeAgentTui


class FakeClient:
    def __init__(self) -> None:
        self.thread_id = str(uuid4())
        self.turns: list[tuple[str, str]] = []
        self.runs: list[dict[str, object]] = []

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
        return {
            "id": self.thread_id,
            "title": title,
            "context_kind": context_kind or "workspace",
            "context_path": context_path,
            "repository_id": repository_id,
            "default_model": default_model,
            "sandbox_profile": sandbox_profile,
            "logical_workspace_path": "/mnt/user-data/workspace/",
        }

    def stream_turn(
        self,
        thread_id: str,
        content: str,
        *,
        model: str | None = None,
    ) -> list[ConversationStreamEvent]:
        self.turns.append((thread_id, content))
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
async def test_tui_details_toggles_verbose_state() -> None:
    app = AwesomeAgentTui(api_url="http://127.0.0.1:8000", client=FakeClient())

    async with app.run_test() as pilot:
        await pilot.click("#prompt")
        await pilot.press("/", "d", "e", "t", "a", "i", "l", "s", "enter")
        transcript = app.query_one("#transcript").render()

    assert app.state.details_enabled is True
    assert "Details enabled" in str(transcript)


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
