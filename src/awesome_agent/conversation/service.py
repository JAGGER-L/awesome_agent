from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Protocol
from uuid import UUID, uuid4

from awesome_agent.conversation.events import (
    ConversationStreamEvent,
    ConversationStreamEventKind,
)
from awesome_agent.conversation.models import (
    ThreadMessageKind,
    ThreadMessageRole,
)
from awesome_agent.conversation.repository import ConversationRepository
from awesome_agent.domain.enums import RunIntent, RunMode
from awesome_agent.domain.models import Run
from awesome_agent.modeling.errors import (
    ModelErrorCode,
    ModelErrorInfo,
    ModelProviderError,
)
from awesome_agent.modeling.messages import AssistantMessage, SystemMessage, UserMessage
from awesome_agent.modeling.provider import ModelProvider
from awesome_agent.modeling.stream import (
    ReasoningDelta,
    ReasoningStarted,
    TextDelta,
    TurnCompleted,
    TurnFailed,
)
from awesome_agent.modeling.turns import ModelRequest, ModelUsage


class ThreadRunIntake(Protocol):
    async def create_run(
        self,
        *,
        repository_id: UUID,
        goal: str,
        intent: RunIntent,
        mode: RunMode = RunMode.SOLO,
    ) -> Run:
        pass


class MissingThreadRepositoryContext(RuntimeError):
    pass


class ConversationService:
    def __init__(
        self,
        *,
        repository: ConversationRepository,
        provider_factory: Callable[[str], ModelProvider],
        default_model: str,
    ) -> None:
        self._repository = repository
        self._provider_factory = provider_factory
        self._default_model = default_model

    async def start_turn(
        self,
        *,
        thread_id: UUID,
        content: str,
        model: str | None = None,
    ) -> AsyncIterator[ConversationStreamEvent]:
        turn_id = uuid4()
        trace_id = uuid4().hex
        sequence = 1
        selected_model = model or self._default_model
        yield _event(
            ConversationStreamEventKind.TURN_STARTED,
            thread_id=thread_id,
            turn_id=turn_id,
            sequence=sequence,
            trace_id=trace_id,
            payload={"model": selected_model},
        )
        user_message = await self._repository.append_message(
            thread_id=thread_id,
            role=ThreadMessageRole.USER,
            content=content,
        )
        sequence += 1
        yield _event(
            ConversationStreamEventKind.MESSAGE_CREATED,
            thread_id=thread_id,
            turn_id=turn_id,
            sequence=sequence,
            trace_id=trace_id,
            payload=user_message.model_dump(mode="json"),
        )

        assistant_text = ""
        reasoning_active = False
        try:
            provider = self._provider_factory(selected_model)
            request = await self._model_request(thread_id)
            async for model_event in provider.stream(request):
                if isinstance(model_event, ReasoningStarted):
                    if not reasoning_active:
                        reasoning_active = True
                        sequence += 1
                        yield _event(
                            ConversationStreamEventKind.REASONING_STARTED,
                            thread_id=thread_id,
                            turn_id=turn_id,
                            sequence=sequence,
                            trace_id=trace_id,
                            payload={},
                        )
                elif isinstance(model_event, ReasoningDelta):
                    if not reasoning_active:
                        reasoning_active = True
                        sequence += 1
                        yield _event(
                            ConversationStreamEventKind.REASONING_STARTED,
                            thread_id=thread_id,
                            turn_id=turn_id,
                            sequence=sequence,
                            trace_id=trace_id,
                            payload={},
                        )
                    sequence += 1
                    yield _event(
                        ConversationStreamEventKind.REASONING_DELTA,
                        thread_id=thread_id,
                        turn_id=turn_id,
                        sequence=sequence,
                        trace_id=trace_id,
                        payload={"text": model_event.text},
                    )
                elif isinstance(model_event, TextDelta):
                    assistant_text += model_event.text
                    sequence += 1
                    yield _event(
                        ConversationStreamEventKind.MESSAGE_DELTA,
                        thread_id=thread_id,
                        turn_id=turn_id,
                        sequence=sequence,
                        trace_id=trace_id,
                        payload={"text": model_event.text},
                    )
                elif isinstance(model_event, TurnFailed):
                    if reasoning_active:
                        sequence += 1
                        yield _event(
                            ConversationStreamEventKind.REASONING_COMPLETED,
                            thread_id=thread_id,
                            turn_id=turn_id,
                            sequence=sequence,
                            trace_id=trace_id,
                            payload={"failed": True},
                        )
                        reasoning_active = False
                    sequence += 1
                    yield _error_event(
                        thread_id=thread_id,
                        turn_id=turn_id,
                        sequence=sequence,
                        trace_id=trace_id,
                        error=model_event.error,
                    )
                    return
                elif isinstance(model_event, TurnCompleted):
                    final_text = model_event.turn.assistant.content or assistant_text
                    usage = model_event.turn.usage
                    if reasoning_active:
                        sequence += 1
                        yield _event(
                            ConversationStreamEventKind.REASONING_COMPLETED,
                            thread_id=thread_id,
                            turn_id=turn_id,
                            sequence=sequence,
                            trace_id=trace_id,
                            payload={"failed": False},
                        )
                        reasoning_active = False
                    if _has_usage(usage):
                        sequence += 1
                        yield _event(
                            ConversationStreamEventKind.USAGE_UPDATED,
                            thread_id=thread_id,
                            turn_id=turn_id,
                            sequence=sequence,
                            trace_id=trace_id,
                            payload=usage.model_dump(mode="json"),
                        )
                    assistant = await self._repository.append_message(
                        thread_id=thread_id,
                        role=ThreadMessageRole.ASSISTANT,
                        content=final_text,
                    )
                    completion_payload = {
                        **assistant.model_dump(mode="json"),
                        "requested_model": selected_model,
                        "response_model": model_event.turn.model,
                        "provider": model_event.turn.provider,
                        "response_id": model_event.turn.response_id,
                    }
                    sequence += 1
                    yield _event(
                        ConversationStreamEventKind.MESSAGE_COMPLETED,
                        thread_id=thread_id,
                        turn_id=turn_id,
                        sequence=sequence,
                        trace_id=trace_id,
                        payload=completion_payload,
                    )
                    sequence += 1
                    yield _event(
                        ConversationStreamEventKind.TURN_COMPLETED,
                        thread_id=thread_id,
                        turn_id=turn_id,
                        sequence=sequence,
                        trace_id=trace_id,
                        payload={"status": "completed"},
                    )
                    return
        except ModelProviderError as error:
            if reasoning_active:
                sequence += 1
                yield _event(
                    ConversationStreamEventKind.REASONING_COMPLETED,
                    thread_id=thread_id,
                    turn_id=turn_id,
                    sequence=sequence,
                    trace_id=trace_id,
                    payload={"failed": True},
                )
            sequence += 1
            yield _error_event(
                thread_id=thread_id,
                turn_id=turn_id,
                sequence=sequence,
                trace_id=trace_id,
                error=error.info,
            )
            return
        except Exception as error:
            if reasoning_active:
                sequence += 1
                yield _event(
                    ConversationStreamEventKind.REASONING_COMPLETED,
                    thread_id=thread_id,
                    turn_id=turn_id,
                    sequence=sequence,
                    trace_id=trace_id,
                    payload={"failed": True},
                )
            sequence += 1
            yield _error_event(
                thread_id=thread_id,
                turn_id=turn_id,
                sequence=sequence,
                trace_id=trace_id,
                error=ModelErrorInfo(
                    code=ModelErrorCode.PROVIDER_PROTOCOL,
                    message=str(error),
                    retryable=False,
                    provider="conversation",
                ),
            )
            return

    async def create_thread_run(
        self,
        *,
        thread_id: UUID,
        goal: str,
        intent: RunIntent,
        mode: RunMode,
        run_intake: ThreadRunIntake,
        repository_id: UUID | None = None,
    ) -> Run:
        thread = await self._repository.get_thread(thread_id)
        effective_repository_id = thread.repository_id or repository_id
        if effective_repository_id is None:
            raise MissingThreadRepositoryContext(
                "Thread does not have a repository_id; register a repository "
                "context before starting a Coding Run."
            )
        if thread.repository_id is None:
            thread = await self._repository.bind_repository(
                thread_id,
                effective_repository_id,
            )
        run = await run_intake.create_run(
            repository_id=thread.repository_id or effective_repository_id,
            goal=goal,
            intent=intent,
            mode=mode,
        )
        await self._repository.append_message(
            thread_id=thread_id,
            role=ThreadMessageRole.SYSTEM,
            content=f"Started Coding Run {run.id}: {goal}",
            kind=ThreadMessageKind.RUN,
            run_id=run.id,
            metadata={
                "run_id": str(run.id),
                "goal": goal,
                "status": run.status.value,
                "intent": run.intent.value,
                "mode": run.mode.value,
            },
        )
        return run

    async def list_thread_runs(self, thread_id: UUID) -> list[dict[str, object]]:
        messages = await self._repository.list_messages(thread_id)
        runs = [
            {
                **message.metadata,
                "message_id": str(message.id),
                "run_id": str(message.run_id),
                "created_at": message.created_at.isoformat(),
            }
            for message in messages
            if message.kind is ThreadMessageKind.RUN and message.run_id is not None
        ]
        return list(reversed(runs))

    async def _model_request(self, thread_id: UUID) -> ModelRequest:
        messages = []
        for message in await self._repository.list_messages(thread_id):
            if message.kind is not ThreadMessageKind.MESSAGE:
                continue
            if message.role is ThreadMessageRole.USER:
                messages.append(UserMessage(content=message.content))
            elif message.role is ThreadMessageRole.ASSISTANT:
                messages.append(AssistantMessage(content=message.content))
            elif message.role is ThreadMessageRole.SYSTEM:
                messages.append(SystemMessage(content=message.content))
        return ModelRequest(messages=messages)


def _event(
    kind: ConversationStreamEventKind,
    *,
    thread_id: UUID,
    turn_id: UUID,
    sequence: int,
    trace_id: str,
    payload: dict[str, object],
) -> ConversationStreamEvent:
    return ConversationStreamEvent(
        event=kind,
        thread_id=thread_id,
        turn_id=turn_id,
        sequence=sequence,
        trace_id=trace_id,
        payload=payload,
    )


def _error_event(
    *,
    thread_id: UUID,
    turn_id: UUID,
    sequence: int,
    trace_id: str,
    error: ModelErrorInfo,
) -> ConversationStreamEvent:
    return _event(
        ConversationStreamEventKind.ERROR,
        thread_id=thread_id,
        turn_id=turn_id,
        sequence=sequence,
        trace_id=trace_id,
        payload=error.model_dump(mode="json"),
    )


def _has_usage(usage: ModelUsage) -> bool:
    return any(
        value is not None
        for value in (
            usage.input_tokens,
            usage.output_tokens,
            usage.reasoning_tokens,
            usage.cache_read_tokens,
            usage.cache_write_tokens,
        )
    )
