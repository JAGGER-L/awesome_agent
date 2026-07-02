from pathlib import Path

from awesome_agent.cli.config_flow import ConfigFlowSummary
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


def test_router_config_uses_first_run_summary_when_available(tmp_path: Path) -> None:
    summary = _summary(tmp_path, model_api_key_configured=False)
    state = ChatSessionState.new(first_run_summary=summary)

    message = SlashRouter(FakeSemanticClient()).handle(
        SlashCommand(SlashCommandKind.CONFIG),
        state,
    )

    assert str(summary.user_config) in message.content
    assert "AWESOME_AGENT_DEEPSEEK_API_KEY=missing" in message.content


def test_router_models_marks_missing_key(tmp_path: Path) -> None:
    state = ChatSessionState.new(
        first_run_summary=_summary(tmp_path, model_api_key_configured=False)
    )

    message = SlashRouter(FakeSemanticClient()).handle(
        SlashCommand(SlashCommandKind.MODELS),
        state,
    )

    assert "deepseek-v4-pro" in message.content
    assert "missing AWESOME_AGENT_DEEPSEEK_API_KEY" in message.content


def _summary(
    tmp_path: Path,
    *,
    model_api_key_configured: bool,
) -> ConfigFlowSummary:
    return ConfigFlowSummary(
        home=tmp_path,
        project_root=tmp_path / "project",
        user_config=tmp_path / ".awesome-agent" / "config.yaml",
        project_config=tmp_path / "project" / "awesome-agent.yaml",
        project_env=tmp_path / "project" / ".env",
        user_config_exists=True,
        project_config_exists=False,
        project_env_exists=False,
        model_name="deepseek-v4-pro",
        model_api_key_env="AWESOME_AGENT_DEEPSEEK_API_KEY",
        model_api_key_configured=model_api_key_configured,
    )
