from __future__ import annotations

from awesome_agent.modeling import ModelProvider
from awesome_agent.providers.deepseek import DeepSeekProvider
from awesome_agent.settings import Settings


class ModelProviderFactory:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def coding_available(self) -> bool:
        return self.settings.deepseek_api_key is not None

    def create(self, model: str) -> ModelProvider:
        key = self.settings.deepseek_api_key
        if key is None:
            raise RuntimeError("DeepSeek API key is not configured.")
        return DeepSeekProvider(
            api_key=key.get_secret_value(),
            model=model,
            base_url=self.settings.deepseek_base_url,
            thinking_enabled=self.settings.deepseek_thinking_enabled,
            reasoning_effort=self.settings.deepseek_reasoning_effort,
        )
