from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import UUID

from awesome_agent.conversation.models import ThreadMessageKind, ThreadMessageRole
from awesome_agent.conversation.repository import ConversationRepository
from awesome_agent.domain.enums import DispatchStatus, EventType, RunStatus
from awesome_agent.domain.models import Agent, Run
from awesome_agent.modeling.messages import (
    AssistantMessage,
    ModelMessage,
    SystemMessage,
    UserMessage,
)
from awesome_agent.modeling.provider import ModelProvider
from awesome_agent.modeling.stream import TextDelta, TurnCompleted, TurnFailed
from awesome_agent.modeling.turns import ModelRequest, ModelTurn
from awesome_agent.runtime.agent_loop import ReadOnlyAgentLoop
from awesome_agent.runtime.repository import RuntimeRepository

ConversationGraphState = dict[str, Any]


class ConversationGraph:
    def __init__(
        self,
        *,
        conversations: ConversationRepository,
        runtime: RuntimeRepository,
        provider_factory: Callable[[str], ModelProvider],
        default_model: str,
        agent_loop: ReadOnlyAgentLoop | None = None,
    ) -> None:
        self.conversations = conversations
        self.runtime = runtime
        self.provider_factory = provider_factory
        self.default_model = default_model
        self.agent_loop = agent_loop or ReadOnlyAgentLoop()

    async def execute(self, run: Run, leader: Agent) -> ConversationGraphState:
        created = await self._run_created_payload(run)
        thread_id = UUID(str(created["thread_id"]))
        content = str(created.get("goal") or run.goal)
        selected_model = str(created.get("model") or leader.model or self.default_model)
        thinking = _optional_str(created.get("thinking"))
        running = run.model_copy(
            update={
                "status": RunStatus.RUNNING,
                "dispatch_status": DispatchStatus.EXECUTING,
            }
        )
        await self.runtime.update_run(running)
        await self.runtime.append_event(
            run_id=run.id,
            event_type=EventType.RUN_STATUS_CHANGED,
            payload={
                "status": running.status.value,
                "dispatch_status": running.dispatch_status.value,
            },
            agent_id=leader.id,
        )
        try:
            state = await self._execute_turn(
                run=running,
                leader=leader,
                thread_id=thread_id,
                content=content,
                selected_model=selected_model,
                thinking=thinking,
            )
        except Exception:
            await self._mark_failed(running, leader)
            raise
        completed = running.model_copy(
            update={
                "status": RunStatus.COMPLETED,
                "dispatch_status": DispatchStatus.TERMINAL,
                "result_text": str(state.get("final_answer") or ""),
            }
        )
        await self.runtime.update_run(completed)
        await self.runtime.append_event(
            run_id=run.id,
            event_type=EventType.RUN_STATUS_CHANGED,
            payload={
                "status": completed.status.value,
                "dispatch_status": completed.dispatch_status.value,
            },
            agent_id=leader.id,
        )
        return state

    async def _execute_turn(
        self,
        *,
        run: Run,
        leader: Agent,
        thread_id: UUID,
        content: str,
        selected_model: str,
        thinking: str | None,
    ) -> ConversationGraphState:
        user_message = await self.conversations.append_message(
            thread_id=thread_id,
            role=ThreadMessageRole.USER,
            content=content,
            run_id=run.id,
            metadata={
                "run_id": str(run.id),
                "working_directory": str(run.working_directory)
                if run.working_directory
                else None,
            },
        )
        await self.runtime.append_event(
            run_id=run.id,
            event_type=EventType.MESSAGE_CREATED,
            payload={
                "thread_id": str(thread_id),
                "message_id": str(user_message.id),
                "role": user_message.role.value,
                "content": user_message.content,
                "run_id": str(run.id),
            },
            agent_id=leader.id,
        )

        messages = await self._model_messages(thread_id)
        model_state = await self._run_model(
            run=run,
            leader=leader,
            messages=messages,
            selected_model=selected_model,
            thinking=thinking,
        )
        final_answer = str(model_state["final_answer"])
        usage = model_state["usage"]
        assistant = await self.conversations.append_message(
            thread_id=thread_id,
            role=ThreadMessageRole.ASSISTANT,
            content=final_answer,
            run_id=run.id,
            metadata={
                "run_id": str(run.id),
                "usage": usage.model_dump(mode="json"),
                "response_model": model_state.get("response_model"),
                "provider": model_state.get("provider"),
                "response_id": model_state.get("response_id"),
            },
        )
        await self.runtime.append_event(
            run_id=run.id,
            event_type=EventType.MESSAGE_CREATED,
            payload={
                "thread_id": str(thread_id),
                "message_id": str(assistant.id),
                "role": assistant.role.value,
                "content": assistant.content,
                "run_id": str(run.id),
                "usage": usage.model_dump(mode="json"),
                "response_model": model_state.get("response_model"),
                "provider": model_state.get("provider"),
                "response_id": model_state.get("response_id"),
            },
            agent_id=leader.id,
        )
        return {
            "final_answer": final_answer,
            "result_summary": "Conversation completed.",
            "usage": usage.model_dump(mode="json"),
            "response_model": model_state.get("response_model"),
            "provider": model_state.get("provider"),
            "response_id": model_state.get("response_id"),
        }

    async def _run_model(
        self,
        *,
        run: Run,
        leader: Agent,
        messages: list[ModelMessage],
        selected_model: str,
        thinking: str | None,
    ) -> ConversationGraphState:
        initial_state: ConversationGraphState = {}

        async def model_call(
            current: ConversationGraphState,
        ) -> ConversationGraphState:
            return await self.agent_loop.wrap_model_call(
                current,
                run=run,
                agent=leader,
                messages=messages,
                handler=lambda _state: self._model_complete(
                    selected_model,
                    messages,
                    thinking,
                ),
            )

        async def after_model(state: ConversationGraphState) -> ConversationGraphState:
            return await self.agent_loop.after_model(
                state,
                run=run,
                agent=leader,
                messages=messages,
                handler=_identity_state,
            )

        before_state: ConversationGraphState = await self.agent_loop.before_model(
            initial_state,
            run=run,
            agent=leader,
            messages=messages,
            handler=model_call,
        )
        return await after_model(before_state)

    async def _run_created_payload(self, run: Run) -> dict[str, object]:
        for event in await self.runtime.list_events(run.id):
            if event.event_type is EventType.RUN_CREATED:
                return event.payload
        raise RuntimeError("Conversation Run is missing run.created payload.")

    async def _model_messages(self, thread_id: UUID) -> list[ModelMessage]:
        messages: list[ModelMessage] = []
        for message in await self.conversations.list_messages(thread_id):
            if message.kind is not ThreadMessageKind.MESSAGE:
                continue
            if message.role is ThreadMessageRole.USER:
                messages.append(UserMessage(content=message.content))
            elif message.role is ThreadMessageRole.ASSISTANT:
                messages.append(AssistantMessage(content=message.content))
            elif message.role is ThreadMessageRole.SYSTEM:
                messages.append(SystemMessage(content=message.content))
        return messages

    async def _model_complete(
        self,
        selected_model: str,
        messages: list[ModelMessage],
        thinking: str | None,
    ) -> ConversationGraphState:
        provider = self.provider_factory(selected_model)
        final_text = ""
        completed: ModelTurn | None = None
        request = ModelRequest(messages=messages, thinking=thinking)
        async for event in provider.stream(request):
            if isinstance(event, TextDelta):
                final_text += event.text
            elif isinstance(event, TurnFailed):
                raise RuntimeError(event.error.message)
            elif isinstance(event, TurnCompleted):
                completed = event.turn
        if completed is None:
            raise RuntimeError("Provider stream ended without a completed turn.")
        final_answer = completed.assistant.content or final_text
        return {
            "final_answer": final_answer,
            "usage": completed.usage,
            "response_model": completed.model,
            "provider": completed.provider,
            "response_id": completed.response_id,
        }

    async def _mark_failed(self, run: Run, leader: Agent) -> None:
        failed = run.model_copy(
            update={
                "status": RunStatus.FAILED,
                "dispatch_status": DispatchStatus.TERMINAL,
            }
        )
        await self.runtime.update_run(failed)
        await self.runtime.append_event(
            run_id=run.id,
            event_type=EventType.RUN_STATUS_CHANGED,
            payload={
                "status": failed.status.value,
                "dispatch_status": failed.dispatch_status.value,
            },
            agent_id=leader.id,
        )


async def _identity_state(state: ConversationGraphState) -> ConversationGraphState:
    return state


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)
