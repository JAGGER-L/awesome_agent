from __future__ import annotations

from dataclasses import dataclass
from math import floor
from typing import cast

from pydantic import JsonValue

from awesome_agent.agent import AgentCompressionResult, AgentState, PreparedAgentContext
from awesome_agent.application.commands import (
    CommandIntent,
    CommandResult,
    CommandStatus,
)
from awesome_agent.context import (
    CompressionRequest,
    CompressionResult,
    CompressionStatus,
    ContextBuilder,
    ContextRequest,
    ContextSource,
    ContextSourceKind,
    ExplicitPathSnapshot,
    ThreadCompressor,
    calculate_context_budget,
    parse_explicit_paths,
    snapshot_explicit_paths,
)
from awesome_agent.conversation import (
    ConversationConflict,
    ConversationService,
    ThreadEntryKind,
    Turn,
)
from awesome_agent.core.workspace import WorkspaceIdentity


@dataclass(frozen=True, slots=True)
class TurnContextCapture:
    natural_input: str
    snapshots: tuple[ExplicitPathSnapshot, ...]


class ApplicationContextService:
    def __init__(
        self,
        *,
        conversation: ConversationService,
        workspace: WorkspaceIdentity,
        builder: ContextBuilder,
        compressor: ThreadCompressor,
        configured_total_tokens: int,
        model_context_limit: int,
        product_instructions: str,
        workspace_instructions: str = "",
    ) -> None:
        self._conversation = conversation
        self._workspace = workspace
        self._builder = builder
        self._compressor = compressor
        self._configured_total_tokens = configured_total_tokens
        self._model_context_limit = model_context_limit
        self._product_instructions = product_instructions
        self._workspace_instructions = workspace_instructions
        self._captures: dict[str, TurnContextCapture] = {}

    def prepare_turn(self, turn: Turn, content: str) -> None:
        parsed = parse_explicit_paths(content)
        budget = calculate_context_budget(
            self._configured_total_tokens,
            self._model_context_limit,
        )
        snapshots = snapshot_explicit_paths(
            self._workspace,
            parsed.references,
            token_budget=floor(budget.effective_input_limit * 0.25),
        )
        self._captures[turn.id] = TurnContextCapture(
            natural_input=parsed.text,
            snapshots=snapshots,
        )

    async def build(self, state: AgentState) -> PreparedAgentContext:
        capture = self._captures.get(state["turn_id"])
        if capture is None:
            raise RuntimeError("Turn context was not prepared.")
        view = self._conversation.read_thread(state["thread_id"])
        turn = next(item for item in view.turns if item.id == state["turn_id"])
        summary_end = view.summary.covered_entry_sequence if view.summary else 0
        sources: list[ContextSource] = [
            ContextSource(
                kind=ContextSourceKind.PRODUCT_INSTRUCTIONS,
                source_id="product",
                content=self._product_instructions,
                role="system",
                mandatory=True,
            )
        ]
        if self._workspace_instructions:
            sources.append(
                ContextSource(
                    kind=ContextSourceKind.WORKSPACE_INSTRUCTIONS,
                    source_id=self._workspace.key,
                    content=self._workspace_instructions,
                    role="system",
                    mandatory=True,
                )
            )
        if view.summary is not None:
            sources.append(
                ContextSource(
                    kind=ContextSourceKind.THREAD_SUMMARY,
                    source_id=f"summary:{view.summary.content_hash}",
                    content=view.summary.content,
                    covered_sequence_start=1,
                    covered_sequence_end=view.summary.covered_entry_sequence,
                )
            )
        for entry in view.entries:
            if entry.id == turn.user_entry_id or entry.sequence <= summary_end:
                continue
            kind = (
                ContextSourceKind.DIRECT_COMMAND
                if entry.kind is ThreadEntryKind.DIRECT_COMMAND
                else ContextSourceKind.RECENT_TURNS
            )
            sources.append(
                ContextSource(
                    kind=kind,
                    source_id=entry.id,
                    content=entry.content,
                    covered_sequence_start=entry.sequence,
                    covered_sequence_end=entry.sequence,
                )
            )
        for snapshot in capture.snapshots:
            sources.append(
                ContextSource(
                    kind=ContextSourceKind.EXPLICIT_PATH,
                    source_id=snapshot.relative_path,
                    content=snapshot.content,
                    mandatory=True,
                    token_budget=max(1, snapshot.estimated_tokens),
                )
            )
        sources.append(
            ContextSource(
                kind=ContextSourceKind.CURRENT_INPUT,
                source_id=turn.user_entry_id,
                content=capture.natural_input,
                mandatory=True,
            )
        )
        prepared = await self._builder.prepare(
            ContextRequest(
                sources=tuple(sources),
                configured_total_tokens=self._configured_total_tokens,
                model_context_limit=self._model_context_limit,
            )
        )
        return PreparedAgentContext(
            messages=prepared.messages,
            manifest=tuple(item.model_dump(mode="json") for item in prepared.manifest),
            estimated_input_tokens=prepared.estimated_input_tokens,
            effective_input_limit=prepared.effective_input_limit,
            compression_recommended=prepared.compression_recommended,
        )

    async def compress(self, state: AgentState) -> AgentCompressionResult:
        view = self._conversation.read_thread(state["thread_id"])
        result = await self._compressor.compact(
            CompressionRequest(
                view=view,
                provider=state["provider"],  # type: ignore[arg-type]
                model=state["model"],
            )
        )
        if result.status is CompressionStatus.COMPLETED and result.summary is not None:
            try:
                self._conversation.store_summary(
                    result.summary,
                    expected=view.summary,
                )
            except ConversationConflict:
                return AgentCompressionResult(
                    completed=False,
                    attempted=True,
                    usage=result.usage,
                    error_code="compression_conflict",
                )
            prepared = await self.build(state)
            return AgentCompressionResult(
                completed=True,
                attempted=True,
                prepared=prepared,
                usage=result.usage,
            )
        return AgentCompressionResult(
            completed=False,
            attempted=result.status is not CompressionStatus.NOOP,
            usage=result.usage,
            error_code=result.error_code,
        )

    async def compact_thread(
        self,
        thread_id: str,
        *,
        provider: str,
        model: str,
    ) -> CompressionResult:
        view = self._conversation.read_thread(thread_id)
        result = await self._compressor.compact(
            CompressionRequest(
                view=view,
                provider=provider,  # type: ignore[arg-type]
                model=model,
            )
        )
        if result.status is CompressionStatus.COMPLETED and result.summary is not None:
            self._conversation.store_summary(result.summary, expected=view.summary)
        return result

    def inspect(self, thread_id: str) -> dict[str, object]:
        view = self._conversation.read_thread(thread_id)
        latest = view.turns[-1] if view.turns else None
        return {
            "manifest": list(latest.context_manifest) if latest else [],
            "summary_covered_entry_sequence": (
                view.summary.covered_entry_sequence if view.summary else 0
            ),
            "summary_covered_turn_count": (
                view.summary.covered_turn_count if view.summary else 0
            ),
        }

    async def context_command(
        self,
        intent: CommandIntent,
        *,
        thread_id: str,
    ) -> CommandResult:
        if intent.arguments:
            return CommandResult(
                status=CommandStatus.ERROR,
                content="Usage: /context",
                data={"error_code": "invalid_arguments"},
            )
        return CommandResult(
            status=CommandStatus.SUCCESS,
            data=cast(dict[str, JsonValue], self.inspect(thread_id)),
        )

    async def compact_command(
        self,
        intent: CommandIntent,
        *,
        thread_id: str,
        provider: str,
        model: str,
    ) -> CommandResult:
        if intent.arguments:
            return CommandResult(
                status=CommandStatus.ERROR,
                content="Usage: /compact",
                data={"error_code": "invalid_arguments"},
            )
        before = self._conversation.read_thread(thread_id).summary
        result = await self.compact_thread(
            thread_id,
            provider=provider,
            model=model,
        )
        if result.status is CompressionStatus.FAILED:
            return CommandResult(
                status=CommandStatus.ERROR,
                content="Context compression failed.",
                data={"error_code": result.error_code or "compression_failed"},
            )
        after = self._conversation.read_thread(thread_id).summary
        return CommandResult(
            status=CommandStatus.SUCCESS,
            data={
                "old_covered_entry_sequence": (
                    before.covered_entry_sequence if before else 0
                ),
                "new_covered_entry_sequence": (
                    after.covered_entry_sequence if after else 0
                ),
                "usage": cast(
                    JsonValue,
                    result.usage.model_dump(mode="json"),
                ),
            },
        )
