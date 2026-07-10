from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from awesome_agent.modeling.catalog import SelectedModel
from awesome_agent.modeling.errors import (
    ModelErrorCode,
    ModelProviderError,
    ProviderProtocolError,
    error_from_info,
)
from awesome_agent.modeling.provider import ModelProvider
from awesome_agent.modeling.stream import (
    ModelStreamEvent,
    ReasoningDelta,
    ReasoningStarted,
    TextDelta,
    ToolArgumentsDelta,
    ToolCallStarted,
    TurnCompleted,
    TurnFailed,
)
from awesome_agent.modeling.turns import ModelRequest, ModelTurn, ProviderId

type AsyncSleeper = Callable[[float], Awaitable[None]]


class RetryPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_retries: int = Field(default=2, ge=0, le=6)
    base_delay_seconds: float = Field(default=0.5, ge=0.0, le=10.0)
    max_delay_seconds: float = Field(default=8.0, ge=0.0, le=30.0)

    @model_validator(mode="after")
    def validate_delay_order(self) -> Self:
        if self.base_delay_seconds > self.max_delay_seconds:
            raise ValueError("Retry base delay cannot exceed maximum delay.")
        return self


class ProviderRetrying(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Annotated[str, Field(pattern=r"^provider\.retrying$")] = "provider.retrying"
    attempt: int = Field(ge=2)
    maximum: int = Field(ge=1)
    delay_seconds: float = Field(ge=0.0)
    error_code: ModelErrorCode


type GatewayEvent = ModelStreamEvent | ProviderRetrying

_VISIBLE_EVENTS = (
    ReasoningStarted,
    ReasoningDelta,
    TextDelta,
    ToolCallStarted,
    ToolArgumentsDelta,
)


class ModelGateway:
    def __init__(
        self,
        providers: Mapping[ProviderId, ModelProvider],
        *,
        retry_policy: RetryPolicy,
        sleeper: AsyncSleeper,
    ) -> None:
        copied = dict(providers)
        for provider_id, provider in copied.items():
            if provider.provider_id != provider_id:
                raise ValueError("Provider mapping key does not match provider_id.")
        self._providers = copied
        self._retry_policy = retry_policy
        self._sleeper = sleeper

    async def stream(
        self,
        selected: SelectedModel,
        request: ModelRequest,
    ) -> AsyncIterator[GatewayEvent]:
        provider = self._providers.get(selected.provider)
        if provider is None:
            raise ProviderProtocolError(
                "Selected Provider is not available in this Gateway.",
                provider=selected.provider,
            )
        retries = 0
        while True:
            visible = False
            completed_seen = False
            retry_error: ModelProviderError | None = None
            try:
                async for event in provider.stream(request):
                    if isinstance(event, _VISIBLE_EVENTS):
                        visible = True
                    if isinstance(event, TurnCompleted):
                        completed_seen = True
                        yield _with_retry_usage(event, retries)
                        continue
                    if isinstance(event, TurnFailed):
                        error = error_from_info(event.error)
                        if (
                            error.info.retryable
                            and not visible
                            and not completed_seen
                            and retries < self._retry_policy.max_retries
                        ):
                            retry_error = error
                            break
                        yield event
                        return
                    yield event
            except asyncio.CancelledError:
                raise
            except ModelProviderError as error:
                if (
                    error.info.retryable
                    and not visible
                    and not completed_seen
                    and retries < self._retry_policy.max_retries
                ):
                    retry_error = error
                else:
                    yield TurnFailed(error=error.info)
                    return
            except Exception:
                protocol_error = ProviderProtocolError(
                    "Provider stream raised an unexpected exception.",
                    provider=selected.provider,
                )
                yield TurnFailed(error=protocol_error.info)
                return
            if retry_error is None:
                return
            delay = min(
                self._retry_policy.base_delay_seconds * (2**retries),
                self._retry_policy.max_delay_seconds,
            )
            retries += 1
            yield ProviderRetrying(
                attempt=retries + 1,
                maximum=self._retry_policy.max_retries + 1,
                delay_seconds=delay,
                error_code=retry_error.info.code,
            )
            await self._sleeper(delay)

    async def complete(
        self,
        selected: SelectedModel,
        request: ModelRequest,
    ) -> ModelTurn:
        completed: list[ModelTurn] = []
        async for event in self.stream(selected, request):
            if isinstance(event, TurnFailed):
                raise error_from_info(event.error)
            if isinstance(event, TurnCompleted):
                completed.append(event.turn)
        if len(completed) != 1:
            raise ProviderProtocolError(
                "Gateway requires exactly one completed model turn.",
                provider=selected.provider,
            )
        return completed[0]


def _with_retry_usage(event: TurnCompleted, retries: int) -> TurnCompleted:
    if retries == 0:
        return event
    usage = event.turn.usage.model_copy(
        update={"provider_retries": event.turn.usage.provider_retries + retries}
    )
    return event.model_copy(
        update={"turn": event.turn.model_copy(update={"usage": usage})}
    )
