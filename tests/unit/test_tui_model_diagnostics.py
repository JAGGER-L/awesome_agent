from __future__ import annotations

from pathlib import Path

from awesome_agent.cli.config_flow import ConfigFlowSummary
from awesome_agent.cli.slash_commands import SlashCommand, SlashCommandKind
from awesome_agent.tui.chat_state import ChatSessionState
from awesome_agent.tui.slash_router import SlashRouter


class ModelClient:
    def list_models(self) -> list[dict[str, object]]:
        return [
            {
                "role": "leader",
                "name": "deepseek-v4-pro",
                "provider": "deepseek",
                "configured": True,
                "api_key_env": "AWESOME_AGENT_DEEPSEEK_API_KEY",
                "api_key_present": True,
                "base_url": "https://api.deepseek.com",
            }
        ]


def test_models_output_includes_last_turn_metadata() -> None:
    state = ChatSessionState.new().note_model_metadata(
        {
            "requested_model": "deepseek-v4-pro",
            "response_model": "deepseek-v4-pro",
            "provider": "deepseek",
            "response_id": "response-123",
        }
    )

    message = SlashRouter(ModelClient()).handle(
        SlashCommand(SlashCommandKind.MODELS),
        state,
    )

    assert "Models" in message.content
    assert "leader: deepseek-v4-pro" in message.content
    assert "configured=yes" in message.content
    assert "base_url: https://api.deepseek.com" in message.content
    assert "last turn: requested=deepseek-v4-pro" in message.content
    assert "response_id=response-123" in message.content
    assert "self-description is not authoritative" in message.content


def test_models_output_uses_first_run_summary_without_secret(tmp_path: Path) -> None:
    state = ChatSessionState.new(
        first_run_summary=ConfigFlowSummary(
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
    )

    message = SlashRouter(ModelClient()).handle(
        SlashCommand(SlashCommandKind.MODELS),
        state,
    )

    assert "default: deepseek-v4-pro" in message.content
    assert "present=no" in message.content
    assert "AWESOME_AGENT_DEEPSEEK_API_KEY" in message.content
