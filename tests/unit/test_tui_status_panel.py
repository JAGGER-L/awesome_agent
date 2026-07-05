from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from awesome_agent.cli.config_flow import ConfigFlowSummary
from awesome_agent.cli.repo_context import CliLaunchContext
from awesome_agent.tui.chat_state import ChatSessionState
from awesome_agent.tui.status_panel import (
    StatusPanelTab,
    build_status_panel_snapshot,
    render_status_panel,
)


def test_status_tab_renders_claude_style_session_snapshot(tmp_path: Path) -> None:
    state = ChatSessionState.new(
        launch_context=CliLaunchContext(
            project_root=tmp_path,
            context_kind="workspace",
        )
    ).with_backend_thread(
        "thread-1",
        title="Snake game",
        context_label=str(tmp_path),
    )

    snapshot = build_status_panel_snapshot(
        state=state,
        config_summary={},
        memory_summary={},
        usage_summary={},
        session_elapsed=timedelta(seconds=75),
    )

    rendered = render_status_panel(snapshot, StatusPanelTab.STATUS).plain

    assert "[Status]" in rendered
    assert "Config" in rendered
    assert "Usage" in rendered
    assert "Version:            -" in rendered
    assert "Conversation name:  Snake game" in rendered
    assert "Thread ID:          thread-1" in rendered
    assert f"cwd:                {tmp_path}" in rendered
    assert "Model:              deepseek-v4-pro" in rendered


def test_config_tab_uses_first_run_summary_and_memory_split(tmp_path: Path) -> None:
    summary = ConfigFlowSummary(
        home=tmp_path,
        project_root=tmp_path / "project",
        user_config=tmp_path / ".awesome-agent" / "config.yaml",
        project_config=tmp_path / "project" / "awesome-agent.yaml",
        project_env=tmp_path / "project" / ".env",
        awesome_env=tmp_path / ".awesome-agent" / ".env",
        user_config_exists=True,
        project_config_exists=False,
        project_env_exists=False,
        awesome_env_exists=False,
        model_name="deepseek-v4-pro",
        model_api_key_env="AWESOME_AGENT_DEEPSEEK_API_KEY",
        model_api_key_configured=False,
    )
    state = ChatSessionState.new(first_run_summary=summary)

    snapshot = build_status_panel_snapshot(
        state=state,
        config_summary={
            "deepseek_base_url": "https://api.deepseek.com",
            "local_cli_sandbox_backend": "local",
        },
        memory_summary={
            "builtin_enabled": True,
            "provider_enabled": False,
            "provider_status": "disabled",
        },
        usage_summary={},
        session_elapsed=timedelta(seconds=0),
    )

    rendered = render_status_panel(snapshot, StatusPanelTab.CONFIG).plain

    assert "[Config]" in rendered
    assert f"Project config:     {summary.project_config} (missing)" in rendered
    assert f"Awesome env:        {summary.awesome_env} (missing)" in rendered
    assert "Provider:           deepseek" in rendered
    assert "API key:            AWESOME_AGENT_DEEPSEEK_API_KEY (missing)" in rendered
    assert "Base URL:           https://api.deepseek.com" in rendered
    assert "Memory:             builtin on, provider off" in rendered
    assert "Sandbox:            local" in rendered


def test_usage_tab_formats_session_duration_and_tokens() -> None:
    snapshot = build_status_panel_snapshot(
        state=ChatSessionState.new().with_backend_thread("thread-1"),
        config_summary={},
        memory_summary={},
        usage_summary={"input_tokens": 1200, "output_tokens": 340},
        session_elapsed=timedelta(seconds=125),
    )

    rendered = render_status_panel(snapshot, StatusPanelTab.USAGE).plain

    assert "[Usage]" in rendered
    assert "Total cost:         -" in rendered
    assert "Total duration:     2m 5s" in rendered
    assert "Usage:              1,200 input, 340 output" in rendered


def test_usage_without_backend_thread_defaults_to_zero_tokens() -> None:
    snapshot = build_status_panel_snapshot(
        state=ChatSessionState.new(),
        config_summary={},
        memory_summary={},
        usage_summary={},
        session_elapsed=timedelta(seconds=0),
    )

    rendered = render_status_panel(snapshot, StatusPanelTab.USAGE).plain

    assert "Usage:              0 input, 0 output" in rendered
