from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import Literal, cast

from awesome_agent.config.loader import LoadedConfigSources
from awesome_agent.config.models import (
    SUPPORTED_MODEL_IDS,
    ApplicationConfig,
    BudgetConfig,
    ProjectBudgetConfig,
    SkillSourceConfig,
    StartupOverrides,
    ThreadConfigState,
    TurnConfig,
)

_PROVIDER_DEFAULTS = {
    "deepseek": "deepseek/deepseek-v4-flash",
    "kimi": "kimi/kimi-k2.6",
}
_SKILL_MODE_PATTERN = re.compile(r"^(?:auto|off|[a-z][a-z0-9_-]{0,63})$")


class ConfigurationResolutionError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def resolve_application_config(sources: LoadedConfigSources) -> ApplicationConfig:
    user = sources.user
    project = sources.workspace
    project_budgets = project.budgets if project is not None else None
    budgets = _restrict_budgets(user.budgets, project_budgets)
    return ApplicationConfig(
        providers=user.providers,
        budgets=budgets,
        memory=user.memory,
        user_skills=tuple(
            SkillSourceConfig(name=name, enabled=False)
            for name in user.skills.disabled
        ),
        workspace_skills=tuple(
            SkillSourceConfig(name=name, enabled=False)
            for name in (project.skills.disabled if project is not None else ())
        ),
        user_mcp_servers=user.mcp_servers,
        workspace_mcp_servers=(project.mcp_servers if project is not None else ()),
        secret_status=sources.secret_status,
    )


def resolve_turn_config(
    application: ApplicationConfig,
    *,
    thread: ThreadConfigState,
    cli: StartupOverrides | None = None,
    environ: Mapping[str, str] | None = None,
    model_context_limit: int | None = None,
) -> TurnConfig:
    overrides = cli or StartupOverrides()
    env = os.environ if environ is None else environ
    model = _select_model(application, thread, overrides, env)
    provider = cast(
        Literal["deepseek", "kimi"],
        model.split("/", maxsplit=1)[0],
    )
    _require_provider_credential(application, provider)
    thinking = _select_thinking(thread, overrides, env)
    skill_mode = _select_skill_mode(application, thread, overrides, env)
    budgets = application.budgets
    if model_context_limit is not None:
        if model_context_limit < 1:
            raise ConfigurationResolutionError(
                "configuration_invalid",
                "Model context limit must be positive.",
            )
        budgets = budgets.model_copy(
            update={
                "total_context_tokens": min(
                    budgets.total_context_tokens,
                    model_context_limit,
                )
            }
        )
    return TurnConfig(
        provider=provider,
        model=model,
        thinking_enabled=thinking,
        skill_mode=skill_mode,
        budgets=budgets,
    )


def _restrict_budgets(
    user: BudgetConfig,
    project: ProjectBudgetConfig | None,
) -> BudgetConfig:
    if project is None:
        return user
    return BudgetConfig(
        model_calls=_minimum(user.model_calls, project.model_calls),
        tool_calls=_minimum(user.tool_calls, project.tool_calls),
        provider_retries=_minimum(
            user.provider_retries,
            project.provider_retries,
        ),
        compressions=_minimum(user.compressions, project.compressions),
        active_execution_seconds=_minimum(
            user.active_execution_seconds,
            project.active_execution_seconds,
        ),
        total_context_tokens=_minimum(
            user.total_context_tokens,
            project.total_context_tokens,
        ),
    )


def _minimum(user: int, project: int | None) -> int:
    return user if project is None else min(user, project)


def _select_model(
    application: ApplicationConfig,
    thread: ThreadConfigState,
    cli: StartupOverrides,
    environ: Mapping[str, str],
) -> str:
    candidate = (
        cli.model
        or environ.get("AWESOME_MODEL")
        or thread.model
        or application.providers.default_model
    )
    if candidate is not None:
        if candidate not in SUPPORTED_MODEL_IDS:
            raise ConfigurationResolutionError(
                "configuration_invalid",
                "Selected model is not in the curated catalog.",
            )
        return candidate
    configured = _configured_providers(application)
    if len(configured) != 1:
        raise ConfigurationResolutionError(
            "model_not_configured",
            "Select a Provider/model before starting an Agent Turn.",
        )
    return _PROVIDER_DEFAULTS[configured[0]]


def _configured_providers(application: ApplicationConfig) -> tuple[str, ...]:
    result: list[str] = []
    if application.secret_status.deepseek_api_key:
        result.append("deepseek")
    if application.secret_status.moonshot_api_key:
        result.append("kimi")
    return tuple(result)


def _require_provider_credential(
    application: ApplicationConfig,
    provider: str,
) -> None:
    configured = provider in _configured_providers(application)
    if not configured:
        raise ConfigurationResolutionError(
            "provider_not_configured",
            f"{provider} credentials are not configured.",
        )


def _select_thinking(
    thread: ThreadConfigState,
    cli: StartupOverrides,
    environ: Mapping[str, str],
) -> bool:
    if cli.thinking_enabled is not None:
        return cli.thinking_enabled
    raw = environ.get("AWESOME_THINKING")
    if raw is not None:
        normalized = raw.strip().casefold()
        if normalized not in {"on", "off"}:
            raise ConfigurationResolutionError(
                "configuration_invalid",
                "AWESOME_THINKING must be on or off.",
            )
        return normalized == "on"
    if thread.thinking_enabled is not None:
        return thread.thinking_enabled
    return False


def _select_skill_mode(
    application: ApplicationConfig,
    thread: ThreadConfigState,
    cli: StartupOverrides,
    environ: Mapping[str, str],
) -> str:
    candidate = cli.skill_mode or environ.get("AWESOME_SKILL") or thread.skill_mode
    if candidate is None:
        return "auto"
    if _SKILL_MODE_PATTERN.fullmatch(candidate) is None:
        raise ConfigurationResolutionError(
            "configuration_invalid",
            "Skill mode is invalid.",
        )
    disabled = {
        skill.name
        for skill in (*application.user_skills, *application.workspace_skills)
        if not skill.enabled
    }
    if candidate in disabled:
        raise ConfigurationResolutionError(
            "skill_disabled",
            "Selected Skill is disabled.",
        )
    return candidate
