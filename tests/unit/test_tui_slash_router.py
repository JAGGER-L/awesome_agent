from awesome_agent.cli.slash_commands import SlashCommand, SlashCommandKind
from awesome_agent.tui.chat_state import ChatSessionState
from awesome_agent.tui.slash_router import SlashRouter


class FakeSemanticClient:
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

    def list_threads(self) -> list[dict[str, object]]:
        return []

    def list_skills(self) -> list[dict[str, object]]:
        return [{"name": "brainstorming", "enabled": True}]

    def list_tools(self) -> dict[str, list[str]]:
        return {"builtin": ["read_file"], "mcp": [], "sandbox": ["shell"]}

    def mcp_status(self) -> list[dict[str, object]]:
        return []

    def list_uploads(self) -> list[dict[str, object]]:
        return []

    def list_current_artifacts(self, run_id: str | None) -> list[dict[str, object]]:
        return []

    def usage_summary(self, run_id: str | None) -> dict[str, object]:
        return {"tokens": 0}

    def config_summary(self) -> dict[str, object]:
        return {"home": "~/.awesome-agent"}


def test_router_handles_tools_command() -> None:
    message = SlashRouter(FakeSemanticClient()).handle(
        SlashCommand(SlashCommandKind.TOOLS),
        ChatSessionState.new(),
    )

    assert "builtin: read_file" in message.content
    assert "sandbox: shell" in message.content


def test_router_toggles_details() -> None:
    message = SlashRouter(FakeSemanticClient()).handle(
        SlashCommand(SlashCommandKind.DETAILS),
        ChatSessionState.new(),
    )

    assert "Verbose activity rendering" in message.content
