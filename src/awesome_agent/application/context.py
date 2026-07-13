from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from math import floor
from typing import Literal, cast

from pydantic import TypeAdapter

from awesome_agent.agent import AgentCompressionResult, AgentState, PreparedAgentContext
from awesome_agent.application.command_results import (
    CommandOutcome,
    CompactCommandPayload,
    ContextCategory,
    ContextCommandPayload,
    error,
    result,
)
from awesome_agent.application.commands import CommandIntent
from awesome_agent.context import (
    CompressionRequest,
    CompressionResult,
    CompressionStatus,
    ContextBuilder,
    ContextManifestItem,
    ContextRequest,
    ContextSource,
    ContextSourceKind,
    ExplicitPathSnapshot,
    Mem0ContextResult,
    ThreadCompressor,
    calculate_context_budget,
    local_memory_context_sources,
    model_identity_context_source,
    parse_explicit_paths,
    snapshot_explicit_paths,
)
from awesome_agent.conversation import (
    ConversationConflict,
    ConversationService,
    ThreadEntryKind,
    ThreadView,
    Turn,
    TurnStatus,
)
from awesome_agent.conversation.models import UsageSummary
from awesome_agent.core.workspace import WorkspaceIdentity
from awesome_agent.extensions.skills import SkillLoader
from awesome_agent.memory import LocalMemoryService, MemoryScope
from awesome_agent.modeling import ModelIdentitySnapshot, ModelMessage, ProviderId

_MODEL_MESSAGE: TypeAdapter[ModelMessage] = TypeAdapter(ModelMessage)
type Mem0Recall = Callable[
    [str, tuple[str, ...]],
    Awaitable[Mem0ContextResult],
]
type ModelIdentityResolver = Callable[[Turn], ModelIdentitySnapshot]
_FROZEN_KINDS = frozenset(
    {
        ContextSourceKind.PRODUCT_INSTRUCTIONS,
        ContextSourceKind.WORKSPACE_INSTRUCTIONS,
        ContextSourceKind.SKILL,
        ContextSourceKind.EXPLICIT_PATH,
        ContextSourceKind.CURRENT_INPUT,
        ContextSourceKind.OPEN_TOOL_CHAIN,
    }
)
_CONTEXT_CATEGORY = {
    ContextSourceKind.PRODUCT_INSTRUCTIONS: "instructions",
    ContextSourceKind.WORKSPACE_INSTRUCTIONS: "instructions",
    ContextSourceKind.SKILL: "instructions",
    ContextSourceKind.THREAD_SUMMARY: "conversation",
    ContextSourceKind.RECENT_TURNS: "conversation",
    ContextSourceKind.DIRECT_COMMAND: "conversation",
    ContextSourceKind.CURRENT_INPUT: "conversation",
    ContextSourceKind.OPEN_TOOL_CHAIN: "conversation",
    ContextSourceKind.EXPLICIT_PATH: "files",
    ContextSourceKind.USER_MEMORY: "memory",
    ContextSourceKind.WORKSPACE_MEMORY: "memory",
    ContextSourceKind.MEM0: "memory",
}


@dataclass(frozen=True, slots=True)
class TurnContextCapture:
    natural_input: str
    snapshots: tuple[ExplicitPathSnapshot, ...]
    memory_sources: tuple[ContextSource, ...] = ()


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
        model_identity: ModelIdentityResolver | None = None,
        workspace_instructions: str = "",
        skill_loader: SkillLoader | None = None,
        local_memory: LocalMemoryService | None = None,
        mem0_recall: Mem0Recall | None = None,
    ) -> None:
        self._conversation = conversation
        self._workspace = workspace
        self._builder = builder
        self._compressor = compressor
        self._configured_total_tokens = configured_total_tokens
        self._model_context_limit = model_context_limit
        self._product_instructions = product_instructions
        self._model_identity = model_identity
        self._workspace_instructions = workspace_instructions
        self._skill_loader = skill_loader
        self._local_memory = local_memory
        self._mem0_recall = mem0_recall
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
        memory_sources: tuple[ContextSource, ...] = ()
        if self._local_memory is not None and self._local_memory.enabled:
            memory_sources = local_memory_context_sources(
                user=self._local_memory.snapshot(MemoryScope.USER),
                workspace=self._local_memory.snapshot(MemoryScope.WORKSPACE),
            )
        self._captures[turn.id] = TurnContextCapture(
            natural_input=parsed.text,
            snapshots=snapshots,
            memory_sources=memory_sources,
        )

    def current_input(self, turn_id: str) -> str:
        capture = self._captures.get(turn_id)
        return "" if capture is None else capture.natural_input

    async def build(self, state: AgentState) -> PreparedAgentContext:
        capture = self._captures.get(state["turn_id"])
        if capture is None:
            raise RuntimeError("Turn context was not prepared.")
        view = self._conversation.read_thread(state["thread_id"])
        turn = next(item for item in view.turns if item.id == state["turn_id"])
        sources: list[ContextSource] = [
            ContextSource(
                kind=ContextSourceKind.PRODUCT_INSTRUCTIONS,
                source_id="product",
                content=self._product_instructions,
                role="system",
                mandatory=True,
            )
        ]
        if self._model_identity is not None:
            sources.append(
                model_identity_context_source(
                    self._model_identity(turn),
                    workspace_path=str(self._workspace.canonical_path),
                )
            )
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
        if self._skill_loader is not None and turn.skill_mode not in {"auto", "off"}:
            skill = self._skill_loader.load(turn.skill_mode)
            sources.append(
                ContextSource(
                    kind=ContextSourceKind.SKILL,
                    source_id=skill.descriptor.name,
                    content=skill.body,
                    role="system",
                    mandatory=True,
                )
            )
        sources.extend(capture.memory_sources)
        if self._mem0_recall is not None:
            local_contents = (
                tuple(
                    entry.content
                    for scope in (MemoryScope.USER, MemoryScope.WORKSPACE)
                    for entry in self._local_memory.snapshot(scope).entries
                )
                if self._local_memory is not None and self._local_memory.enabled
                else ()
            )
            recalled = await self._mem0_recall(capture.natural_input, local_contents)
            if recalled.source is not None:
                sources.append(recalled.source)
        sources.extend(_history_sources(view, turn))
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
        return await self._prepare_sources(sources)

    async def _prepare_sources(
        self,
        sources: list[ContextSource],
    ) -> PreparedAgentContext:
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
                provider=cast(ProviderId, state["provider"]),
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
            prepared = (
                await self.build(state)
                if state["turn_id"] in self._captures
                else await self._build_from_frozen(state)
            )
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

    async def _build_from_frozen(self, state: AgentState) -> PreparedAgentContext:
        view = self._conversation.read_thread(state["thread_id"])
        turn = next(item for item in view.turns if item.id == state["turn_id"])
        sources = _history_sources(view, turn)
        for manifest, raw_message in zip(
            state["context_manifest"],
            state["messages"],
            strict=False,
        ):
            try:
                kind = ContextSourceKind(str(manifest["kind"]))
            except (KeyError, ValueError):
                continue
            if kind not in _FROZEN_KINDS:
                continue
            message = _MODEL_MESSAGE.validate_python(raw_message)
            content = message.content
            if content.startswith("[") and "\n" in content:
                content = content.split("\n", 1)[1]
            role = (
                message.role
                if message.role in {"system", "user", "assistant"}
                else "user"
            )
            sources.append(
                ContextSource(
                    kind=kind,
                    source_id=str(manifest.get("source_id") or kind.value),
                    content=content,
                    role=cast(Literal["system", "user", "assistant"], role),
                    mandatory=True,
                )
            )
        return await self._prepare_sources(sources)

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
                provider=cast(ProviderId, provider),
                model=model,
            )
        )
        if result.status is CompressionStatus.COMPLETED and result.summary is not None:
            self._conversation.store_summary(result.summary, expected=view.summary)
        return result

    def inspect(self, thread_id: str) -> dict[str, object]:
        view = self._conversation.read_thread(thread_id)
        manifest = self._conversation.latest_context_manifest(thread_id)
        return {
            "manifest": list(manifest),
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
    ) -> CommandOutcome:
        if intent.arguments:
            return error("invalid_arguments", "Usage: /context")
        manifest = self._conversation.latest_context_manifest(thread_id)
        totals = {
            name: 0 for name in ("instructions", "conversation", "files", "memory")
        }
        for raw in manifest:
            item = ContextManifestItem.model_validate(raw)
            totals[_CONTEXT_CATEGORY[item.kind]] += item.estimated_tokens
        category_names: tuple[
            Literal["instructions", "conversation", "files", "memory"], ...
        ] = ("instructions", "conversation", "files", "memory")
        categories = tuple(
            ContextCategory(name=name, estimated_tokens=totals[name])
            for name in category_names
        )
        return result(
            ContextCommandPayload(
                categories=categories,
                total_tokens=sum(item.estimated_tokens for item in categories),
                budget_tokens=self._configured_total_tokens,
            )
        )

    async def compact_command(
        self,
        intent: CommandIntent,
        *,
        thread_id: str,
        provider: str,
        model: str,
    ) -> CommandOutcome:
        if intent.arguments:
            return error("invalid_arguments", "Usage: /compact")
        before = self._conversation.read_thread(thread_id).summary
        compression = await self.compact_thread(
            thread_id,
            provider=provider,
            model=model,
        )
        if compression.status is CompressionStatus.FAILED:
            return error(
                compression.error_code or "compression_failed",
                "Context compression failed.",
            )
        after = self._conversation.read_thread(thread_id).summary
        return result(
            CompactCommandPayload(
                old_covered_entry_sequence=(
                    before.covered_entry_sequence if before else 0
                ),
                new_covered_entry_sequence=(
                    after.covered_entry_sequence if after else 0
                ),
                usage=UsageSummary(
                    **compression.usage.model_dump(mode="python"),
                    model_calls=1,
                    compressions=1,
                ),
            )
        )


def _history_sources(view: ThreadView, turn: Turn) -> list[ContextSource]:
    summary_end = view.summary.covered_entry_sequence if view.summary else 0
    sources: list[ContextSource] = []
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
    completed_ids = {
        identifier
        for item in view.turns
        if item.status is TurnStatus.COMPLETED
        for identifier in (item.user_entry_id, item.assistant_entry_id)
        if identifier is not None
    }
    for entry in view.entries:
        if entry.id == turn.user_entry_id or entry.sequence <= summary_end:
            continue
        if (
            entry.kind is not ThreadEntryKind.DIRECT_COMMAND
            and entry.id not in completed_ids
        ):
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
    return sources
