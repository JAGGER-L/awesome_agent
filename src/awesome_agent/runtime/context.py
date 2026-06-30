from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel, TypeAdapter

from awesome_agent.artifacts.repository import ArtifactMetadataRepository
from awesome_agent.artifacts.store import LocalArtifactStore
from awesome_agent.modeling.messages import (
    AssistantMessage,
    ModelMessage,
    SystemMessage,
    ToolResultMessage,
    UserMessage,
)
from awesome_agent.runtime.token_accounting import (
    TokenAccountant,
    default_token_accountant,
)

_MESSAGE_ADAPTER = TypeAdapter(list[ModelMessage])
_SUMMARY_SNIPPET_CHARS = 240
_SUMMARY_MAX_CHARS = 8_000


@dataclass(frozen=True, slots=True)
class ContextPolicy:
    soft_context_tokens: int
    hard_context_tokens: int
    recent_context_tokens: int


@dataclass(frozen=True, slots=True)
class PreparedContext:
    request_messages: list[ModelMessage]
    rolling_summary: str
    compacted: bool
    hard_limit_exceeded: bool
    artifact_refs: list[str] = field(default_factory=list)
    before_estimated_tokens: int = 0
    after_estimated_tokens: int = 0
    removed_message_count: int = 0


class SummaryProvider(Protocol):
    async def summarize(
        self,
        *,
        prior_summary: str,
        removed_messages: Sequence[dict[str, Any]],
        artifact_refs: Sequence[str],
    ) -> str:
        """Return a compact deterministic summary for checkpoint state."""
        ...


class DeterministicSummaryProvider:
    async def summarize(
        self,
        *,
        prior_summary: str,
        removed_messages: Sequence[dict[str, Any]],
        artifact_refs: Sequence[str],
    ) -> str:
        sections: list[str] = []
        if prior_summary.strip():
            sections.append(f"Previous summary:\n{prior_summary.strip()}")
        if removed_messages:
            sections.append(
                "Compacted messages:\n"
                + "\n".join(_summarize_message(message) for message in removed_messages)
            )
        if artifact_refs:
            sections.append(
                "Artifacts: " + ", ".join(str(ref) for ref in artifact_refs)
            )
        summary = "\n\n".join(sections).strip()
        if len(summary) > _SUMMARY_MAX_CHARS:
            return summary[: _SUMMARY_MAX_CHARS - 18].rstrip() + "\n...[truncated]"
        return summary


class ContextManager:
    def __init__(
        self,
        *,
        summary_provider: SummaryProvider,
        artifact_store: LocalArtifactStore | None = None,
        artifact_repository: ArtifactMetadataRepository | None = None,
        token_accountant: TokenAccountant | None = None,
    ) -> None:
        self._summary_provider = summary_provider
        self._artifact_store = artifact_store
        self._artifact_repository = artifact_repository
        self._token_accountant = token_accountant or default_token_accountant()

    async def prepare_request(
        self,
        *,
        run_id: UUID,
        agent_id: UUID | None,
        runtime_route: str,
        messages: Sequence[ModelMessage | Mapping[str, Any]],
        rolling_summary: str,
        policy: ContextPolicy,
    ) -> PreparedContext:
        parsed = _coerce_messages(messages)
        before_tokens = self._token_accountant.estimate_messages(parsed).tokens
        if before_tokens < policy.soft_context_tokens:
            return PreparedContext(
                request_messages=parsed,
                rolling_summary=rolling_summary,
                compacted=False,
                hard_limit_exceeded=False,
                before_estimated_tokens=before_tokens,
                after_estimated_tokens=before_tokens,
            )

        keep_indexes = _required_context_indexes(parsed)
        keep_indexes.update(
            _recent_context_indexes(
                parsed,
                keep_indexes,
                policy,
                token_accountant=self._token_accountant,
            )
        )
        removed_messages = [
            _message_to_json(message)
            for index, message in enumerate(parsed)
            if index not in keep_indexes
        ]

        artifact_refs: list[str] = []
        if removed_messages:
            artifact_refs.extend(
                await self._write_removed_messages_artifact(
                    run_id=run_id,
                    agent_id=agent_id,
                    runtime_route=runtime_route,
                    removed_messages=removed_messages,
                )
            )

        kept_messages = [parsed[index] for index in sorted(keep_indexes)]
        kept_messages = await self._offload_large_tool_results(
            run_id=run_id,
            agent_id=agent_id,
            messages=kept_messages,
            policy=policy,
            artifact_refs=artifact_refs,
        )

        updated_summary = await self._summary_provider.summarize(
            prior_summary=rolling_summary,
            removed_messages=removed_messages,
            artifact_refs=artifact_refs,
        )
        request_messages = _insert_summary_message(kept_messages, updated_summary)
        after_tokens = self._token_accountant.estimate_messages(request_messages).tokens
        return PreparedContext(
            request_messages=request_messages,
            rolling_summary=updated_summary,
            compacted=bool(removed_messages or artifact_refs),
            hard_limit_exceeded=(
                after_tokens >= policy.hard_context_tokens
                or _required_non_tool_exceeds_hard_limit(
                    parsed,
                    policy,
                    token_accountant=self._token_accountant,
                )
            ),
            artifact_refs=artifact_refs,
            before_estimated_tokens=before_tokens,
            after_estimated_tokens=after_tokens,
            removed_message_count=len(removed_messages),
        )

    async def _write_removed_messages_artifact(
        self,
        *,
        run_id: UUID,
        agent_id: UUID | None,
        runtime_route: str,
        removed_messages: Sequence[dict[str, Any]],
    ) -> list[str]:
        if self._artifact_store is None or self._artifact_repository is None:
            return []
        payload = {
            "runtime_route": runtime_route,
            "removed_messages": list(removed_messages),
        }
        content = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        ).encode("utf-8")
        metadata = self._artifact_store.write(
            run_id=run_id,
            agent_id=agent_id,
            artifact_type="context-compaction",
            filename="removed-messages.json",
            content=content,
            mime_type="application/json",
            summary=(
                f"Removed {len(removed_messages)} messages during "
                f"{runtime_route} context compaction."
            ),
        )
        await self._artifact_repository.record(metadata)
        return [str(metadata.id)]

    async def _offload_large_tool_results(
        self,
        *,
        run_id: UUID,
        agent_id: UUID | None,
        messages: Sequence[ModelMessage],
        policy: ContextPolicy,
        artifact_refs: list[str],
    ) -> list[ModelMessage]:
        compacted: list[ModelMessage] = []
        for message in messages:
            if (
                isinstance(message, ToolResultMessage)
                and self._token_accountant.estimate_text(message.content).tokens
                > policy.recent_context_tokens
            ):
                compacted.append(
                    await self._offload_tool_result(
                        run_id=run_id,
                        agent_id=agent_id,
                        message=message,
                        artifact_refs=artifact_refs,
                    )
                )
            else:
                compacted.append(message)
        return compacted

    async def _offload_tool_result(
        self,
        *,
        run_id: UUID,
        agent_id: UUID | None,
        message: ToolResultMessage,
        artifact_refs: list[str],
    ) -> ToolResultMessage:
        if self._artifact_store is None or self._artifact_repository is None:
            return message
        metadata = self._artifact_store.write(
            run_id=run_id,
            agent_id=agent_id,
            artifact_type="tool-output",
            filename=f"{message.call_id}.txt",
            content=message.content.encode("utf-8"),
            mime_type="text/plain",
            summary=f"Large tool result for {message.call_id}",
        )
        await self._artifact_repository.record(metadata)
        artifact_ref = str(metadata.id)
        artifact_refs.append(artifact_ref)
        return message.model_copy(
            update={
                "content": (
                    f"Large tool result offloaded to artifact {artifact_ref}; "
                    f"{len(message.content)} characters preserved outside checkpoint."
                ),
                "artifact_refs": [*message.artifact_refs, artifact_ref],
            }
        )


def _coerce_messages(
    messages: Sequence[ModelMessage | Mapping[str, Any]],
) -> list[ModelMessage]:
    payload: list[dict[str, Any]] = []
    for message in messages:
        if isinstance(message, BaseModel):
            payload.append(message.model_dump(mode="json"))
        else:
            payload.append(dict(message))
    return list(_MESSAGE_ADAPTER.validate_python(payload))


def _required_context_indexes(messages: Sequence[ModelMessage]) -> set[int]:
    indexes: set[int] = set()
    for index, message in enumerate(messages):
        if isinstance(message, SystemMessage):
            indexes.add(index)
            break
    for index, message in enumerate(messages):
        if isinstance(message, UserMessage):
            indexes.add(index)
            break
    indexes.update(_latest_tool_cycle_indexes(messages))
    return indexes


def _latest_tool_cycle_indexes(messages: Sequence[ModelMessage]) -> set[int]:
    for assistant_index in range(len(messages) - 1, -1, -1):
        message = messages[assistant_index]
        if not isinstance(message, AssistantMessage) or not message.tool_calls:
            continue
        call_ids = {tool_call.call_id for tool_call in message.tool_calls}
        result_indexes: set[int] = set()
        for index in range(assistant_index + 1, len(messages)):
            candidate = messages[index]
            if (
                isinstance(candidate, ToolResultMessage)
                and candidate.call_id in call_ids
            ):
                result_indexes.add(index)
        if result_indexes:
            return {assistant_index, *result_indexes}
    return set()


def _recent_context_indexes(
    messages: Sequence[ModelMessage],
    required_indexes: set[int],
    policy: ContextPolicy,
    *,
    token_accountant: TokenAccountant,
) -> set[int]:
    selected: set[int] = set()
    used_tokens = token_accountant.estimate_messages(
        [messages[index] for index in sorted(required_indexes)]
    ).tokens
    for index in range(len(messages) - 1, -1, -1):
        if index in required_indexes:
            continue
        message_tokens = token_accountant.estimate_messages([messages[index]]).tokens
        if used_tokens + message_tokens > policy.recent_context_tokens:
            continue
        selected.add(index)
        used_tokens += message_tokens
    return selected


def _insert_summary_message(
    messages: Sequence[ModelMessage],
    summary: str,
) -> list[ModelMessage]:
    if not summary:
        return list(messages)
    summary_message = SystemMessage(content=f"Prior context summary:\n{summary}")
    for index, message in enumerate(messages):
        if isinstance(message, SystemMessage):
            return [*messages[: index + 1], summary_message, *messages[index + 1 :]]
    return [summary_message, *messages]


def _required_non_tool_exceeds_hard_limit(
    messages: Sequence[ModelMessage],
    policy: ContextPolicy,
    *,
    token_accountant: TokenAccountant,
) -> bool:
    required = _required_context_indexes(messages)
    for index in required:
        message = messages[index]
        if isinstance(message, ToolResultMessage):
            continue
        if token_accountant.estimate_messages([message]).tokens >= (
            policy.hard_context_tokens
        ):
            return True
    return False


def _message_to_json(message: ModelMessage) -> dict[str, Any]:
    if isinstance(message, BaseModel):
        return message.model_dump(mode="json")
    raise TypeError(f"Unsupported message type: {type(message)!r}")


def _summarize_message(message: Mapping[str, Any]) -> str:
    role = str(message.get("role", "unknown"))
    parts = [role]
    call_id = message.get("call_id")
    if call_id:
        parts.append(f"call_id={call_id}")
    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, list) and tool_calls:
        tool_names = [
            str(call.get("name"))
            for call in tool_calls
            if isinstance(call, Mapping) and call.get("name")
        ]
        if tool_names:
            parts.append("tools=" + ",".join(tool_names))
    content = str(message.get("content", "")).replace("\n", " ").strip()
    if len(content) > _SUMMARY_SNIPPET_CHARS:
        content = content[: _SUMMARY_SNIPPET_CHARS - 15].rstrip() + "...[truncated]"
    if content:
        parts.append(content)
    return "- " + " | ".join(parts)
