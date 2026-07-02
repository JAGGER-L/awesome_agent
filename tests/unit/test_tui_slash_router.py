from awesome_agent.cli.slash_commands import parse_slash_command
from awesome_agent.tui.chat_state import ChatSessionState
from awesome_agent.tui.slash_router import SlashRouter


class FakeClient:
    def create_thread(self, title: str) -> dict[str, object]:
        return {
            "id": "thread-1",
            "title": title,
            "workspace_path": "~/.awesome-agent/threads/thread-1/workspace",
            "logical_workspace": "/mnt/user-data/workspace",
        }

    def runtime_status(self) -> dict[str, object]:
        return {"api": "ready", "sandbox": "local"}

    def list_models(self) -> list[dict[str, object]]:
        return [{"name": "deepseek-v4-pro", "role": "leader"}]

    def memory_summary(self) -> dict[str, object]:
        return {"enabled": False, "items": 0}


def test_status_command_uses_runtime_status() -> None:
    state = ChatSessionState.new()
    message = SlashRouter(FakeClient()).handle(parse_slash_command("/status"), state)

    assert "api=ready" in message.content
    assert "sandbox=local" in message.content


def test_models_command_lists_models() -> None:
    state = ChatSessionState.new()
    message = SlashRouter(FakeClient()).handle(parse_slash_command("/models"), state)

    assert "deepseek-v4-pro" in message.content


def test_memory_command_reports_memory() -> None:
    state = ChatSessionState.new()
    message = SlashRouter(FakeClient()).handle(parse_slash_command("/memory"), state)

    assert "enabled=False" in message.content


def test_new_command_creates_durable_thread() -> None:
    state = ChatSessionState.new()
    message = SlashRouter(FakeClient()).handle(
        parse_slash_command("/new Snake game"),
        state,
    )

    assert "thread-1" in message.content
    assert "Snake game" in message.content
    assert "/mnt/user-data/workspace" in message.content
