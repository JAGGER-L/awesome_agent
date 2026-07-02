import pytest

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
