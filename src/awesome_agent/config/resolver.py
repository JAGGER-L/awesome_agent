from __future__ import annotations

import os
import re
from collections.abc import Mapping

from awesome_agent.config.loader import LoadedConfigSources
from awesome_agent.config.models import (
    ApplicationConfig,
    BudgetConfig,
    ProjectBudgetConfig,
    SkillSourceConfig,
    StartupOverrides,
    ThreadConfigState,
    TurnConfig,
    UserBudgetConfig,
    WebConfig,
)
from awesome_agent.modeling import MODEL_CATALOG, ModelCatalogError, ProviderId

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
    web = _restrict_web(
        user.web,
        project.web.blocked_domains if project is not None else (),
    )
    return ApplicationConfig(
        providers=user.providers,
        budgets=budgets,
        web=web,
        memory=user.memory,
        user_skills=tuple(
            SkillSourceConfig(name=name, enabled=False) for name in user.skills.disabled
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
) -> TurnConfig:
    overrides = cli or StartupOverrides()
    env = os.environ if environ is None else environ
    model = _select_model(application, thread, overrides, env)
    provider = MODEL_CATALOG.provider_for_model(model).id
    thinking = _select_thinking(thread, overrides, env)
    skill_mode = _select_skill_mode(application, thread, overrides, env)
    budgets = application.budgets
    model_context_limit = MODEL_CATALOG.profile(model).context_limit
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
    user: UserBudgetConfig,
    project: ProjectBudgetConfig | None,
) -> BudgetConfig:
    project = project or ProjectBudgetConfig()
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
        web_requests=_minimum(user.web_requests, project.web_requests),
    )


def _minimum(user: int, project: int | None) -> int:
    return user if project is None else min(user, project)


def _restrict_web(
    user: WebConfig,
    project_blocked_domains: tuple[str, ...],
) -> WebConfig:
    blocked_domains = tuple(
        dict.fromkeys((*user.blocked_domains, *project_blocked_domains))
    )
    if len(blocked_domains) > 128:
        raise ConfigurationResolutionError(
            "configuration_invalid",
            "Effective Web blocked domains exceed the 128-domain limit.",
        )
    return WebConfig(
        enabled=user.enabled,
        provider=user.provider,
        blocked_domains=blocked_domains,
    )


def _select_model(
    application: ApplicationConfig,
    thread: ThreadConfigState,
    cli: StartupOverrides,
    environ: Mapping[str, str],
) -> str:
    candidate: str | None
    if cli.model is not None:
        candidate = cli.model
    elif "AWESOME_MODEL" in environ:
        candidate = environ["AWESOME_MODEL"]
    elif thread.model is not None:
        candidate = thread.model
    else:
        candidate = application.providers.default_model
    configured = _configured_providers(application)
    try:
        return MODEL_CATALOG.require_selection(
            candidate,
            configured_providers=configured,
        ).model
    except ModelCatalogError as error:
        code = (
            "configuration_invalid"
            if error.code in {"unsupported_model", "unsupported_provider"}
            else error.code
        )
        raise ConfigurationResolutionError(code, error.message) from error


def _configured_providers(application: ApplicationConfig) -> tuple[ProviderId, ...]:
    result: list[ProviderId] = []
    if application.secret_status.deepseek_api_key:
        result.append("deepseek")
    if application.secret_status.moonshot_api_key:
        result.append("kimi")
    return tuple(result)


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
    return True


def _select_skill_mode(
    application: ApplicationConfig,
    thread: ThreadConfigState,
    cli: StartupOverrides,
    environ: Mapping[str, str],
) -> str:
    candidate: str | None
    if cli.skill_mode is not None:
        candidate = cli.skill_mode
    elif "AWESOME_SKILL" in environ:
        candidate = environ["AWESOME_SKILL"]
    else:
        candidate = thread.skill_mode
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
