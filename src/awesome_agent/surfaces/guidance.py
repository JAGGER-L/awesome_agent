from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from awesome_agent.cli.config_flow import ConfigFlowSummary

OFFICIAL_DEEPSEEK_BASE_URL = "https://api.deepseek.com"


class GuidanceSeverity(StrEnum):
    INFO = "info"
    WARN = "warn"
    ERROR = "error"


class DoctorStatus(StrEnum):
    OK = "OK"
    WARN = "WARN"
    ERROR = "ERROR"
    INFO = "INFO"


@dataclass(frozen=True, slots=True)
class Guidance:
    title: str
    detail: str
    next_steps: tuple[str, ...]
    severity: GuidanceSeverity


@dataclass(frozen=True, slots=True)
class DoctorLine:
    status: DoctorStatus
    label: str
    detail: str

    def render(self) -> str:
        return f"{self.status.value:<5} {self.label}: {self.detail}"


@dataclass(frozen=True, slots=True)
class DoctorReport:
    lines: tuple[DoctorLine, ...]
    next_steps: tuple[str, ...]

    @property
    def exit_code(self) -> int:
        return 1 if any(line.status is DoctorStatus.ERROR for line in self.lines) else 0

    def render(self) -> str:
        rendered = ["Awesome Agent CLI check", ""]
        rendered.extend(line.render() for line in self.lines)
        if self.next_steps:
            rendered.extend(["", "Next:"])
            rendered.extend(f"  {step}" for step in self.next_steps)
        return "\n".join(rendered)


def missing_api_key_guidance(
    env_name: str = "AWESOME_AGENT_DEEPSEEK_API_KEY",
) -> Guidance:
    return Guidance(
        title="API key is missing",
        detail=f"{env_name} is not set in the current environment.",
        next_steps=(
            f"Set {env_name} in your environment.",
            "Restart awesome after changing environment variables.",
            "Run: awesome doctor",
        ),
        severity=GuidanceSeverity.ERROR,
    )


def first_run_guidance(summary: ConfigFlowSummary) -> tuple[Guidance, ...]:
    items: list[Guidance] = []
    if not summary.user_config_exists:
        items.append(
            Guidance(
                title="User config is missing",
                detail=f"{summary.user_config} does not exist yet.",
                next_steps=("Run: awesome init",),
                severity=GuidanceSeverity.WARN,
            )
        )
    if summary.needs_model_setup:
        items.append(missing_api_key_guidance(summary.model_api_key_env))
    return tuple(items)


def guidance_for_model_error(
    code: str | None,
    *,
    provider: str = "deepseek",
) -> Guidance | None:
    normalized = (code or "").casefold()
    env_name = "AWESOME_AGENT_DEEPSEEK_API_KEY"
    if normalized in {
        "authentication",
        "auth_error",
        "unauthorized",
        "invalid_api_key",
        "missing_api_key",
    }:
        return missing_api_key_guidance(env_name)
    if normalized in {"rate_limit", "rate_limited", "too_many_requests"}:
        return Guidance(
            title="Provider rate limit reached",
            detail=(
                f"{provider} rejected the request because the current rate limit "
                "was reached."
            ),
            next_steps=("Wait and retry the last message with Ctrl+R.",),
            severity=GuidanceSeverity.WARN,
        )
    if normalized in {
        "transient",
        "timeout",
        "network_error",
        "provider_unavailable",
    }:
        return Guidance(
            title="Temporary provider failure",
            detail=f"{provider} could not complete the request right now.",
            next_steps=(
                "Check your network connection.",
                "Retry the last message with Ctrl+R.",
            ),
            severity=GuidanceSeverity.WARN,
        )
    if normalized in {"context_length", "context_too_large", "max_context_exceeded"}:
        return Guidance(
            title="Context is too large",
            detail="The request is larger than the model context window.",
            next_steps=(
                "Shorten the request, remove large attachments, or split the task.",
            ),
            severity=GuidanceSeverity.WARN,
        )
    if normalized in {"provider_protocol", "invalid_provider_response"}:
        return Guidance(
            title="Provider returned an unexpected response",
            detail=(
                f"{provider} returned a response Awesome Agent could not parse safely."
            ),
            next_steps=("Keep the request id and error text when reporting this issue.",),
            severity=GuidanceSeverity.ERROR,
        )
    return None


def build_cli_doctor_report(
    summary: ConfigFlowSummary,
    *,
    deepseek_base_url: str,
) -> DoctorReport:
    lines = [
        DoctorLine(
            DoctorStatus.OK if summary.user_config_exists else DoctorStatus.WARN,
            "User config",
            (
                str(summary.user_config)
                if summary.user_config_exists
                else f"{summary.user_config} not found"
            ),
        ),
        DoctorLine(
            DoctorStatus.OK if summary.model_api_key_configured else DoctorStatus.ERROR,
            "API key",
            (
                f"{summary.model_api_key_env} is set"
                if summary.model_api_key_configured
                else f"{summary.model_api_key_env} is missing"
            ),
        ),
        DoctorLine(DoctorStatus.INFO, "cwd", str(summary.project_root)),
        DoctorLine(
            DoctorStatus.INFO,
            "Project config",
            (
                str(summary.project_config)
                if summary.project_config_exists
                else f"{summary.project_config} not found"
            ),
        ),
        DoctorLine(
            DoctorStatus.INFO,
            "Project env",
            (
                str(summary.project_env)
                if summary.project_env_exists
                else f"{summary.project_env} not found"
            ),
        ),
        DoctorLine(
            (
                DoctorStatus.OK
                if deepseek_base_url == OFFICIAL_DEEPSEEK_BASE_URL
                else DoctorStatus.ERROR
            ),
            "Base URL",
            (
                deepseek_base_url
                if deepseek_base_url == OFFICIAL_DEEPSEEK_BASE_URL
                else (
                    f"{deepseek_base_url} is unsupported; "
                    f"use {OFFICIAL_DEEPSEEK_BASE_URL}"
                )
            ),
        ),
    ]
    next_steps: list[str] = []
    if not summary.user_config_exists:
        next_steps.append("Run: awesome init")
    if not summary.model_api_key_configured:
        next_steps.extend(
            missing_api_key_guidance(summary.model_api_key_env).next_steps[:2]
        )
    if deepseek_base_url != OFFICIAL_DEEPSEEK_BASE_URL:
        next_steps.append(
            "Remove AWESOME_AGENT_DEEPSEEK_BASE_URL or set it to "
            f"{OFFICIAL_DEEPSEEK_BASE_URL}."
        )
    if not next_steps:
        next_steps.append("Start: awesome")
    return DoctorReport(lines=tuple(lines), next_steps=tuple(dict.fromkeys(next_steps)))
