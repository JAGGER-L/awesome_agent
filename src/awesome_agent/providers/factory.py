from __future__ import annotations

from collections.abc import Callable

from awesome_agent.modeling import ModelProvider
from awesome_agent.modeling.catalog import (
    DEEPSEEK_OFFICIAL_BASE_URL,
    DEEPSEEK_PROVIDER_ID,
    ModelCatalog,
)
from awesome_agent.providers.deepseek import DeepSeekProvider
from awesome_agent.providers.routing import (
    ModelRouteCandidate,
    ModelRouteRequest,
    RoutedModelProvider,
    StaticModelRouter,
)
from awesome_agent.settings import Settings


class ModelProviderFactory:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.catalog = ModelCatalog.from_settings(settings)

    @property
    def coding_available(self) -> bool:
        try:
            self.catalog.require_supported_configuration()
            self.catalog.require_configured_provider(DEEPSEEK_PROVIDER_ID)
            self.catalog.validate_role_models()
        except Exception:
            return False
        return True

    def create(self, model: str) -> ModelProvider:
        self.catalog.require_supported_configuration()
        self.catalog.require_model(model)
        self.catalog.require_configured_provider(DEEPSEEK_PROVIDER_ID)
        key = self.settings.deepseek_api_key
        assert key is not None
        return DeepSeekProvider(
            api_key=key.get_secret_value(),
            model=model,
            base_url=DEEPSEEK_OFFICIAL_BASE_URL,
            thinking_enabled=self.settings.deepseek_thinking_enabled,
            reasoning_effort=self.settings.deepseek_reasoning_effort,
        )

    def create_candidate(self, candidate: ModelRouteCandidate) -> ModelProvider:
        self.catalog.require_provider(candidate.provider)
        return self.create(candidate.model)

    def create_routed_resolver(
        self,
        *,
        runtime_route: str,
        agent_role: str | None = None,
        task_kind: str | None = None,
    ) -> Callable[[str], ModelProvider]:
        def resolve(model: str) -> ModelProvider:
            default_candidate = ModelRouteCandidate(
                provider="deepseek",
                model=model,
                reason="default",
            )
            return RoutedModelProvider(
                router=StaticModelRouter(default_candidate=default_candidate),
                route_request=ModelRouteRequest(
                    runtime_route=runtime_route,
                    agent_role=agent_role,
                    task_kind=task_kind,
                ),
                provider_factory=self.create_candidate,
            )

        return resolve
