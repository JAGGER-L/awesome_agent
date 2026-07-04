from pathlib import Path

from awesome_agent.cli.config_flow import ConfigFlowSummary
from awesome_agent.cli.slash_commands import (
    SlashCommand,
    SlashCommandKind,
    parse_slash_command,
)
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

    def list_models(self) -> dict[str, object]:
        return _model_catalog(configured=False)

    def memory_summary(self) -> dict[str, object]:
        return {
            "enabled": True,
            "builtin_enabled": True,
            "provider_enabled": False,
            "provider_status": "disabled",
            "files": {
                "user": "C:/Users/test/.awesome-agent/state/memory/USER.md",
                "memory": "C:/Users/test/.awesome-agent/state/memory/MEMORY.md",
            },
            "counts": {"user": 1, "memory": 0},
            "truncated": {"user": False, "memory": False},
        }

    def list_threads(self) -> list[dict[str, object]]:
        return []

    def list_skills(self) -> list[dict[str, object]]:
        return [{"name": "brainstorming", "enabled": True}]

    def list_tools(self) -> dict[str, list[dict[str, object]]]:
        return {
            "Files": [{"name": "read_file", "risk_level": "low", "health": "healthy"}],
            "Terminal": [
                {"name": "run_command", "risk_level": "medium", "health": "healthy"}
            ],
            "Approvals": [{"name": "File edits: ask first", "health": "policy"}],
        }

    def mcp_status(self) -> list[dict[str, object]]:
        return []

    def usage_summary(
        self,
        thread_id: str | None,
        run_id: str | None,
    ) -> dict[str, object]:
        return {"tokens": 0}

    def config_summary(self) -> dict[str, object]:
        return {
            "project_root": "E:/project",
            "mode": "embedded",
            "home": "~/.awesome-agent",
            "default_model": "deepseek-v4-pro",
            "memory_enabled": True,
            "sandbox_backend": "local",
            "deepseek_api_key_configured": False,
        }


def test_router_handles_tools_command() -> None:
    message = SlashRouter(FakeSemanticClient()).handle(
        SlashCommand(SlashCommandKind.TOOLS),
        ChatSessionState.new(),
    )

    assert "Leader tools" in message.content
    assert "Files\n  read_file" in message.content
    assert "Terminal\n  run_command" in message.content
    assert "Approvals\n  File edits: ask first" in message.content


def test_router_toggles_details() -> None:
    message = SlashRouter(FakeSemanticClient()).handle(
        SlashCommand(SlashCommandKind.DETAILS),
        ChatSessionState.new(),
    )

    assert "Details" in message.content
    assert "Off" in message.content
    assert "On" in message.content


def test_router_config_uses_first_run_summary_when_available(tmp_path: Path) -> None:
    summary = _summary(tmp_path, model_api_key_configured=False)
    state = ChatSessionState.new(first_run_summary=summary)

    message = SlashRouter(FakeSemanticClient()).handle(
        SlashCommand(SlashCommandKind.CONFIG),
        state,
    )

    assert "Configuration" in message.content
    assert "Project" in message.content
    assert str(summary.project_config) in message.content
    assert "AWESOME_AGENT_DEEPSEEK_API_KEY: missing" in message.content


def test_router_model_marks_missing_key(tmp_path: Path) -> None:
    state = ChatSessionState.new(
        first_run_summary=_summary(tmp_path, model_api_key_configured=False)
    )

    message = SlashRouter(FakeSemanticClient()).handle(
        SlashCommand(SlashCommandKind.MODEL),
        state,
    )

    assert "deepseek-v4-pro" in message.content
    assert "missing AWESOME_AGENT_DEEPSEEK_API_KEY" in message.content


def test_router_memory_uses_two_level_entry() -> None:
    message = SlashRouter(FakeSemanticClient()).handle(
        SlashCommand(SlashCommandKind.MEMORY),
        ChatSessionState.new(),
    )

    assert "Memory" in message.content
    assert "Builtin: on" in message.content
    assert "Provider: off (disabled)" in message.content
    assert "Entries: user=1 memory=0" in message.content
    assert "USER.md" in message.content


def test_router_config_uses_user_facing_sections() -> None:
    message = SlashRouter(FakeSemanticClient()).handle(
        SlashCommand(SlashCommandKind.CONFIG),
        ChatSessionState.new(),
    )

    assert "Configuration" in message.content
    assert "Project\n  Root:" in message.content
    assert "Runtime\n  Mode:" in message.content
    assert "Secrets\n  DeepSeek API key:" in message.content


def test_router_unknown_command_is_actionable() -> None:
    message = SlashRouter(FakeSemanticClient()).handle(
        SlashCommand(SlashCommandKind.UNKNOWN, "resume"),
        ChatSessionState.new(),
    )

    assert "Unknown command: /resume" in message.content
    assert "Type /help" in message.content


def test_run_slash_command_is_removed() -> None:
    parsed = parse_slash_command("/" + "run build a game")

    assert parsed.kind is SlashCommandKind.UNKNOWN


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


def _model_catalog(*, configured: bool = True) -> dict[str, object]:
    return {
        "providers": [
            {
                "id": "deepseek",
                "display_name": "DeepSeek",
                "configured": configured,
                "credential_env": "AWESOME_AGENT_DEEPSEEK_API_KEY",
                "api_key_present": configured,
                "models": [
                    {
                        "id": "deepseek-v4-pro",
                        "display_name": "DeepSeek V4 Pro",
                        "provider_id": "deepseek",
                        "capabilities": ["streaming", "tools", "reasoning"],
                        "recommended_for": ["leader"],
                        "selected": True,
                    }
                ],
            }
        ],
        "current": {
            "provider_id": "deepseek",
            "model_id": "deepseek-v4-pro",
        },
    }
