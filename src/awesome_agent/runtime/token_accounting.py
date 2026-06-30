from __future__ import annotations

import fnmatch
import json
from collections.abc import Sequence
from dataclasses import dataclass, replace
from math import ceil
from typing import Protocol

from awesome_agent.modeling.messages import AssistantMessage, ModelMessage
from awesome_agent.modeling.tools import ToolDefinition
from awesome_agent.modeling.turns import ModelRequest


class Tokenizer(Protocol):
    def count_text(self, text: str) -> int:
        """Return tokenizer-specific token count for one text segment."""
        ...


@dataclass(frozen=True, slots=True)
class TokenEstimate:
    tokens: int
    estimator: str
    provider: str | None = None
    model: str | None = None
    exact: bool = False
    error_margin_ratio: float = 0.25

    def with_margin(self) -> TokenEstimate:
        if self.exact or self.error_margin_ratio <= 0:
            return self
        return replace(
            self,
            tokens=ceil(self.tokens * (1 + self.error_margin_ratio)),
        )


@dataclass(frozen=True, slots=True)
class ModelTokenProfile:
    provider: str
    model_pattern: str
    estimator_name: str
    tokenizer: Tokenizer | None = None
    message_overhead_tokens: int = 4
    request_overhead_tokens: int = 3
    tool_overhead_tokens: int = 8
    chars_per_token: float = 3.0
    error_margin_ratio: float = 0.25

    @property
    def exact(self) -> bool:
        return self.tokenizer is not None and self.error_margin_ratio == 0


class TokenAccountant:
    def __init__(
        self,
        profiles: Sequence[ModelTokenProfile] | None = None,
    ) -> None:
        self._profiles = list(profiles or default_token_profiles())

    def estimate_text(
        self,
        text: str,
        *,
        provider: str | None = None,
        model: str | None = None,
    ) -> TokenEstimate:
        profile = self._profile_for(provider=provider, model=model)
        return self._estimate_text_with_profile(text, profile, provider, model)

    def estimate_messages(
        self,
        messages: Sequence[ModelMessage],
        *,
        provider: str | None = None,
        model: str | None = None,
    ) -> TokenEstimate:
        profile = self._profile_for(provider=provider, model=model)
        total = profile.request_overhead_tokens
        for message in messages:
            total += profile.message_overhead_tokens
            total += self._raw_text_tokens(
                getattr(message, "content", ""),
                profile,
            )
            if isinstance(message, AssistantMessage):
                for call in message.tool_calls:
                    total += profile.tool_overhead_tokens
                    total += self._raw_text_tokens(call.name, profile)
                    total += self._raw_text_tokens(call.arguments_json, profile)
        return self._estimate(total, profile, provider, model).with_margin()

    def estimate_request(
        self,
        request: ModelRequest,
        *,
        provider: str | None = None,
        model: str | None = None,
    ) -> TokenEstimate:
        profile = self._profile_for(provider=provider, model=model)
        total = self.estimate_messages(
            request.messages,
            provider=provider,
            model=model,
        ).tokens
        for tool in request.tools:
            total += profile.tool_overhead_tokens
            total += self._estimate_tool_definition(tool, profile).tokens
        estimate = self._estimate(total, profile, provider, model)
        return estimate if not request.tools else estimate.with_margin()

    def _estimate_tool_definition(
        self,
        tool: ToolDefinition,
        profile: ModelTokenProfile,
    ) -> TokenEstimate:
        payload = json.dumps(tool.model_dump(mode="json"), sort_keys=True)
        return self._estimate(
            self._raw_text_tokens(payload, profile), profile, None, None
        )

    def _estimate_text_with_profile(
        self,
        text: str,
        profile: ModelTokenProfile,
        provider: str | None,
        model: str | None,
    ) -> TokenEstimate:
        return self._estimate(
            self._raw_text_tokens(text, profile),
            profile,
            provider,
            model,
        ).with_margin()

    def _raw_text_tokens(self, text: str, profile: ModelTokenProfile) -> int:
        if not text:
            return 0
        if profile.tokenizer is not None:
            return profile.tokenizer.count_text(text)
        return _fallback_text_tokens(text, profile.chars_per_token)

    def _estimate(
        self,
        tokens: int,
        profile: ModelTokenProfile,
        provider: str | None,
        model: str | None,
    ) -> TokenEstimate:
        return TokenEstimate(
            tokens=tokens,
            estimator=profile.estimator_name,
            provider=provider,
            model=model,
            exact=profile.exact,
            error_margin_ratio=profile.error_margin_ratio,
        )

    def _profile_for(
        self,
        *,
        provider: str | None,
        model: str | None,
    ) -> ModelTokenProfile:
        for profile in self._profiles:
            provider_matches = provider is None or profile.provider == provider
            model_matches = model is not None and fnmatch.fnmatch(
                model,
                profile.model_pattern,
            )
            if provider_matches and model_matches:
                return profile
        return _unknown_profile()


def default_token_profiles() -> list[ModelTokenProfile]:
    return [
        ModelTokenProfile(
            provider="deepseek",
            model_pattern="deepseek-*",
            estimator_name="deepseek-calibrated-heuristic",
            chars_per_token=3.0,
            error_margin_ratio=0.25,
        ),
        ModelTokenProfile(
            provider="openai",
            model_pattern="gpt-*",
            estimator_name="openai-calibrated-heuristic",
            chars_per_token=3.2,
            error_margin_ratio=0.20,
        ),
        ModelTokenProfile(
            provider="openai",
            model_pattern="o*",
            estimator_name="openai-reasoning-calibrated-heuristic",
            chars_per_token=3.2,
            error_margin_ratio=0.25,
        ),
        _unknown_profile(),
    ]


def default_token_accountant() -> TokenAccountant:
    return TokenAccountant()


def _unknown_profile() -> ModelTokenProfile:
    return ModelTokenProfile(
        provider="unknown",
        model_pattern="*",
        estimator_name="heuristic-char-fallback",
        chars_per_token=3.0,
        error_margin_ratio=0.30,
    )


def _fallback_text_tokens(text: str, chars_per_token: float) -> int:
    non_ascii = sum(1 for char in text if ord(char) > 127)
    ascii_chars = len(text) - non_ascii
    return ceil(ascii_chars / chars_per_token) + non_ascii
