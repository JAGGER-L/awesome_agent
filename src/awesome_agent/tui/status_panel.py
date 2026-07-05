from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from rich.text import Text

from awesome_agent.cli.config_flow import DEFAULT_MODEL_API_KEY_ENV

if TYPE_CHECKING:
    from awesome_agent.tui.chat_state import ChatSessionState


class StatusPanelTab(StrEnum):
    STATUS = "status"
    CONFIG = "config"
    USAGE = "usage"

    @classmethod
    def ordered(cls) -> tuple[StatusPanelTab, ...]:
        return (cls.STATUS, cls.CONFIG, cls.USAGE)

    @property
    def label(self) -> str:
        return {
            StatusPanelTab.STATUS: "Status",
            StatusPanelTab.CONFIG: "Config",
            StatusPanelTab.USAGE: "Usage",
        }[self]


@dataclass(frozen=True, slots=True)
class StatusPanelSnapshot:
    version: str
    conversation_name: str
    thread_id: str
    cwd: str
    model: str
    project_config: str
    project_env: str
    provider: str
    api_key: str
    base_url: str
    memory: str
    sandbox: str
    total_cost: str
    total_duration: str
    usage: str


def build_status_panel_snapshot(
    *,
    state: ChatSessionState,
    config_summary: dict[str, object],
    memory_summary: dict[str, object],
    usage_summary: dict[str, object],
    session_elapsed: timedelta,
) -> StatusPanelSnapshot:
    cwd = _cwd_label(state)
    return StatusPanelSnapshot(
        version="-",
        conversation_name=state.thread_title or "New conversation",
        thread_id=state.backend_thread_id or "-",
        cwd=cwd,
        model=state.current_model or "deepseek-v4-pro",
        project_config=_project_config_label(state, cwd),
        project_env=_project_env_label(state, cwd),
        provider="deepseek",
        api_key=_api_key_label(state, config_summary),
        base_url=_str_value(
            config_summary.get("deepseek_base_url"),
            "https://api.deepseek.com",
        ),
        memory=_memory_label(memory_summary),
        sandbox=_sandbox_label(config_summary),
        total_cost="-",
        total_duration=_duration_label(session_elapsed),
        usage=_usage_label(state, usage_summary),
    )


def render_status_panel(
    snapshot: StatusPanelSnapshot,
    active_tab: StatusPanelTab,
) -> Text:
    rendered = Text("-" * 72, style="dim")
    rendered.append("\n  ")
    for index, tab in enumerate(StatusPanelTab.ordered()):
        if index:
            rendered.append("   ")
        label = f"[{tab.label}]" if tab is active_tab else tab.label
        rendered.append(label, style="bold" if tab is active_tab else "dim")
    rendered.append("\n\n")
    for key, value in _rows_for(snapshot, active_tab):
        rendered.append(f"  {key:<20}{value}\n")
    rendered.rstrip()
    return rendered


def _rows_for(
    snapshot: StatusPanelSnapshot,
    active_tab: StatusPanelTab,
) -> tuple[tuple[str, str], ...]:
    if active_tab is StatusPanelTab.STATUS:
        return (
            ("Version:", snapshot.version),
            ("Conversation name:", snapshot.conversation_name),
            ("Thread ID:", snapshot.thread_id),
            ("cwd:", snapshot.cwd),
            ("Model:", snapshot.model),
        )
    if active_tab is StatusPanelTab.CONFIG:
        return (
            ("Project config:", snapshot.project_config),
            ("Awesome env:", snapshot.project_env),
            ("Provider:", snapshot.provider),
            ("API key:", snapshot.api_key),
            ("Base URL:", snapshot.base_url),
            ("Memory:", snapshot.memory),
            ("Sandbox:", snapshot.sandbox),
        )
    return (
        ("Total cost:", snapshot.total_cost),
        ("Total duration:", snapshot.total_duration),
        ("Usage:", snapshot.usage),
    )


def _cwd_label(state: ChatSessionState) -> str:
    if state.thread_context_label:
        return state.thread_context_label
    if state.launch_context is not None:
        return state.launch_context.display_path
    return "-"


def _project_config_label(state: ChatSessionState, cwd: str) -> str:
    summary = state.first_run_summary
    if summary is not None:
        return _path_with_missing(summary.project_config, summary.project_config_exists)
    if cwd != "-":
        return str(Path(cwd) / "awesome-agent.yaml")
    return "-"


def _project_env_label(state: ChatSessionState, cwd: str) -> str:
    summary = state.first_run_summary
    if summary is not None:
        path = summary.awesome_env or summary.home / ".env"
        return _path_with_missing(path, summary.awesome_env_exists)
    if cwd != "-":
        return "Awesome env"
    return "-"


def _path_with_missing(path: Path, exists: bool | None) -> str:
    suffix = " (missing)" if exists is False else ""
    return f"{path}{suffix}"


def _api_key_label(
    state: ChatSessionState,
    config_summary: dict[str, object],
) -> str:
    summary = state.first_run_summary
    if summary is not None:
        env_name = summary.model_api_key_env
        configured = summary.model_api_key_configured
    else:
        env_name = _str_value(
            config_summary.get("deepseek_api_key_env"),
            DEFAULT_MODEL_API_KEY_ENV,
        )
        configured = config_summary.get("deepseek_api_key_configured") is True
    return f"{env_name} ({'set' if configured else 'missing'})"


def _memory_label(memory_summary: dict[str, object]) -> str:
    builtin = memory_summary.get("builtin_enabled")
    provider = memory_summary.get("provider_enabled")
    provider_status = memory_summary.get("provider_status")
    if not isinstance(builtin, bool) or not isinstance(provider, bool):
        return "unknown"
    provider_label = "on" if provider else "off"
    if isinstance(provider_status, str) and provider_status not in {
        "enabled",
        "healthy",
        "on",
        "disabled",
        "off",
    }:
        provider_label = provider_status
    return f"builtin {'on' if builtin else 'off'}, provider {provider_label}"


def _sandbox_label(config_summary: dict[str, object]) -> str:
    return _str_value(
        config_summary.get("local_cli_sandbox_backend"),
        _str_value(config_summary.get("sandbox_backend"), "-"),
    )


def _usage_label(
    state: ChatSessionState,
    usage_summary: dict[str, object],
) -> str:
    if state.backend_thread_id is None:
        return "0 input, 0 output"
    input_tokens = _int_value(usage_summary.get("input_tokens"))
    output_tokens = _int_value(usage_summary.get("output_tokens"))
    if input_tokens is None or output_tokens is None:
        return "unknown"
    return f"{input_tokens:,} input, {output_tokens:,} output"


def _duration_label(value: timedelta) -> str:
    total = max(0, int(value.total_seconds()))
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m {seconds}s"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def _str_value(value: object, fallback: str) -> str:
    return value if isinstance(value, str) and value else fallback


def _int_value(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None
