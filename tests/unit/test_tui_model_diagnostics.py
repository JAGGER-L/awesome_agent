from __future__ import annotations

from pathlib import Path

from awesome_agent.cli.config_flow import ConfigFlowSummary
from awesome_agent.cli.slash_commands import SlashCommand, SlashCommandKind
from awesome_agent.surfaces.client import SurfaceThread
from awesome_agent.tui.chat_state import ChatSessionState
from awesome_agent.tui.slash_router import ChatSemanticClient, SlashRouter


class ModelClient(ChatSemanticClient):
    def create_thread(self, title: str) -> SurfaceThread | dict[str, object]:
        raise AssertionError("not used by this test")

    def runtime_status(self) -> dict[str, object]:
        raise AssertionError("not used by this test")

    def list_models(self) -> dict[str, object]:
        return {
            "providers": [
                {
                    "id": "deepseek",
                    "display_name": "DeepSeek",
                    "configured": True,
                    "credential_env": "AWESOME_AGENT_DEEPSEEK_API_KEY",
                    "api_key_present": True,
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

    def memory_summary(self) -> dict[str, object]:
        raise AssertionError("not used by this test")

    def list_threads(self) -> list[SurfaceThread | dict[str, object]]:
        raise AssertionError("not used by this test")

    def list_skills(self) -> list[dict[str, object]]:
        raise AssertionError("not used by this test")

    def list_tools(self) -> dict[str, list[dict[str, object]]]:
        raise AssertionError("not used by this test")

    def mcp_status(self) -> list[dict[str, object]]:
        raise AssertionError("not used by this test")

    def usage_summary(
        self,
        thread_id: str | None,
        run_id: str | None,
    ) -> dict[str, object]:
        raise AssertionError("not used by this test")

    def config_summary(self, thread_id: str | None = None) -> dict[str, object]:
        raise AssertionError("not used by this test")


def test_model_output_includes_last_turn_metadata() -> None:
    state = ChatSessionState.new().note_model_metadata(
        {
            "requested_model": "deepseek-v4-pro",
            "response_model": "deepseek-v4-pro",
            "provider": "deepseek",
            "response_id": "response-123",
        }
    )

    message = SlashRouter(ModelClient()).handle(
        SlashCommand(SlashCommandKind.MODEL),
        state,
    )

    assert "Models" in message.content
    assert "DeepSeek" in message.content
    assert "DeepSeek V4 Pro" in message.content
    assert "configured=yes" in message.content
    assert "AWESOME_AGENT_DEEPSEEK_API_KEY" in message.content
    assert "last turn: requested=deepseek-v4-pro" in message.content
    assert "response_id=response-123" in message.content
    assert "self-description is not authoritative" in message.content


def test_model_output_uses_first_run_summary_without_secret(tmp_path: Path) -> None:
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
        SlashCommand(SlashCommandKind.MODEL),
        state,
    )

    assert "deepseek-v4-pro" in message.content
    assert "present=no" in message.content
    assert "AWESOME_AGENT_DEEPSEEK_API_KEY" in message.content
