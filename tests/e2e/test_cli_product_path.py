from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from uuid import uuid4

import pytest

from awesome_agent.cli.repo_context import CliLaunchContext
from awesome_agent.conversation.events import (
    ConversationStreamEvent,
    ConversationStreamEventKind,
)
from awesome_agent.tui.app import AwesomeAgentTui


class HtmlGameSurfaceClient:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.thread_id = str(uuid4())
        self.turns: list[str] = []
        self.threads: list[dict[str, object]] = [
            {
                "id": self.thread_id,
                "title": "Snake game",
                "context_kind": "workspace",
                "context_path": str(workspace),
                "updated_label": "now",
            }
        ]
        self.messages: list[dict[str, object]] = []

    def close(self) -> None:
        return None

    def create_thread(self, title: str, **kwargs: object) -> dict[str, object]:
        self.threads[0]["title"] = title
        return self.threads[0]

    def list_threads(self) -> list[dict[str, object]]:
        return list(self.threads)

    def resume_thread(self, query: str) -> dict[str, object]:
        return self.threads[0]

    def update_thread_settings(
        self,
        thread_id: str,
        *,
        default_model: str | None = None,
        thinking_mode: str | None = None,
        local_memory_enabled: bool | None = None,
        provider_memory: str | None = None,
    ) -> dict[str, object]:
        return self.threads[0]

    def list_thread_messages(self, thread_id: str) -> list[dict[str, object]]:
        return list(self.messages)

    def last_resumable_run(self, thread_id: str) -> dict[str, object] | None:
        return None

    def stream_turn(
        self,
        thread_id: str,
        content: str,
        *,
        model: str | None = None,
        thinking: str | None = None,
        memory: dict[str, object] | None = None,
        skill_ids: tuple[str, ...] = (),
    ) -> Iterable[ConversationStreamEvent]:
        self.turns.append(content)
        target = self.workspace / "snake-game.html"
        target.write_text(
            "<!doctype html><html><title>Snake</title><canvas></canvas></html>",
            encoding="utf-8",
        )
        self.messages.extend(
            [
                {"role": "user", "content": content, "kind": "message"},
                {
                    "role": "assistant",
                    "content": "Created snake-game.html.",
                    "kind": "model",
                    "metadata": {
                        "changed_files": [
                            {"path": "snake-game.html", "status": "created"}
                        ]
                    },
                },
            ]
        )
        turn_id = uuid4()
        yield ConversationStreamEvent(
            event=ConversationStreamEventKind.MESSAGE_DELTA,
            thread_id=uuid4(),
            turn_id=turn_id,
            sequence=1,
            trace_id="trace-html-game",
            payload={"text": "Created snake-game.html."},
        )
        yield ConversationStreamEvent(
            event=ConversationStreamEventKind.MESSAGE_COMPLETED,
            thread_id=uuid4(),
            turn_id=turn_id,
            sequence=2,
            trace_id="trace-html-game",
            payload={
                "content": "Created snake-game.html.",
                "changed_files": [{"path": "snake-game.html", "status": "created"}],
            },
        )

    def runtime_status(self) -> dict[str, object]:
        return {"api": "embedded", "sandbox": "local"}

    def list_thread_runs(self, thread_id: str) -> list[dict[str, object]]:
        return []

    def list_models(self) -> list[dict[str, object]]:
        return [{"name": "fake-model"}]

    def memory_summary(self) -> dict[str, object]:
        return {"enabled": False}

    def local_memory_facts(self, thread_id: str | None) -> list[str]:
        return []

    def list_skills(self) -> list[dict[str, object]]:
        return []

    def list_tools(self) -> dict[str, list[dict[str, object]]]:
        return {"Files": [{"name": "write_file"}], "Terminal": [], "MCP": []}

    def mcp_status(self) -> list[dict[str, object]]:
        return []

    def usage_summary(
        self,
        thread_id: str | None,
        run_id: str | None,
    ) -> dict[str, object]:
        return {"total_tokens": 0}

    def config_summary(self) -> dict[str, object]:
        return {"mode": "embedded"}

    def cancel(
        self,
        run_id: str,
        *,
        thread_id: str | None = None,
    ) -> dict[str, object]:
        return {"id": run_id, "status": "cancelled"}

    def decide_approval(
        self,
        run_id: str,
        approval_id: str,
        *,
        approved: bool,
        thread_id: str | None = None,
    ) -> dict[str, object]:
        return {"status": "decided", "approved": approved}


@pytest.mark.asyncio
async def test_ordinary_input_creates_html_game_in_launch_workspace(
    tmp_path: Path,
) -> None:
    client = HtmlGameSurfaceClient(tmp_path)
    app = AwesomeAgentTui(
        client=client,
        launch_context=CliLaunchContext(
            project_root=tmp_path,
            context_kind="workspace",
        ),
    )

    async with app.run_test() as pilot:
        await pilot.click("#prompt")
        await pilot.press(
            "c",
            "r",
            "e",
            "a",
            "t",
            "e",
            " ",
            "s",
            "n",
            "a",
            "k",
            "e",
            "enter",
        )
        await pilot.pause()
        transcript = app.query_one("#transcript").render()

    assert client.turns == ["create snake"]
    assert (tmp_path / "snake-game.html").is_file()
    rendered = str(transcript)
    assert "Created snake-game.html." in rendered
    assert "Changed files" in rendered
    assert "created snake-game.html" in rendered
