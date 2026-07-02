from pathlib import Path

import pytest

from awesome_agent.cli.config_flow import ConfigFlowSummary
from awesome_agent.cli.repo_context import CliLaunchContext
from awesome_agent.tui.app import AwesomeAgentTui


class FakeClient:
    def create_thread(self, title: str) -> dict[str, object]:
        return {
            "id": "thread-1",
            "title": title,
            "logical_workspace_path": "/mnt/user-data/workspace/",
        }

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
async def test_tui_accepts_plain_message_without_repo_selection_block(
    tmp_path: Path,
) -> None:
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
        await pilot.press("h", "i", "enter")
        transcript = app.query_one("#transcript").render()

    rendered = str(transcript)
    assert "hi" in rendered
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
