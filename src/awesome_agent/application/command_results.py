from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from awesome_agent.application.contracts import (
    ApplicationState,
    OperationAccepted,
    StatusSnapshot,
    ThreadReadResult,
)
from awesome_agent.config.credentials import ProviderCredentialStatuses
from awesome_agent.conversation.models import (
    Thread,
    ThreadEntryKind,
    TurnStatus,
    UsageSummary,
)
from awesome_agent.core.tools.permissions import PermissionMode


class _CommandModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CommandOption(_CommandModel):
    value: str = Field(min_length=1, max_length=200)
    label: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1_000)
    selected: bool = False
    disabled: bool = False


class CommandSelection(_CommandModel):
    kind: Literal["selection"] = "selection"
    prompt: str = Field(min_length=1, max_length=1_000)
    options: tuple[CommandOption, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_options(self) -> Self:
        values = [option.value for option in self.options]
        if len(values) != len(set(values)):
            raise ValueError("Command option values must be unique.")
        if sum(option.selected for option in self.options) > 1:
            raise ValueError("At most one Command option may be selected.")
        return self


class CommandSecretPrompt(_CommandModel):
    kind: Literal["secret"] = "secret"
    provider: Literal["deepseek", "kimi", "mem0", "tavily"]
    action: Literal["add", "replace"]
    label: str = Field(min_length=1, max_length=200)
    environment_variable: str = Field(min_length=1, max_length=128)
    help_url: str = Field(min_length=1, max_length=2_000)


class CommandApplicationInteraction(_CommandModel):
    kind: Literal["application"] = "application"
    interaction_id: str = Field(min_length=1, max_length=128)


CommandInteraction = Annotated[
    CommandSelection | CommandSecretPrompt | CommandApplicationInteraction,
    Field(discriminator="kind"),
]


class NoticeCommandPayload(_CommandModel):
    kind: Literal["notice"] = "notice"
    message: str = Field(min_length=1, max_length=30_000)


class ThreadTransitionSnapshot(_CommandModel):
    reason: Literal["new", "resume", "fork", "retry"]
    application: ApplicationState
    thread: ThreadReadResult

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        if self.application.current_thread_id != self.thread.view.thread.id:
            raise ValueError("Thread transition identities must match.")
        return self


class ThreadTransitionCommandPayload(_CommandModel):
    kind: Literal["thread_transition"] = "thread_transition"
    transition: ThreadTransitionSnapshot

    @model_validator(mode="after")
    def validate_non_retry_transition(self) -> Self:
        transition = self.transition
        reason = transition.reason
        lineage = transition.thread.view.thread.lineage
        if reason == "retry":
            raise ValueError("Retry transitions require a thread_retry payload.")
        if reason == "new" and lineage is not None:
            raise ValueError("New transitions require a root Thread.")
        if reason == "fork" and (lineage is None or lineage.kind != "fork"):
            raise ValueError("Fork transitions require Fork Thread lineage.")
        return self


class ThreadRetryCommandPayload(_CommandModel):
    kind: Literal["thread_retry"] = "thread_retry"
    transition: ThreadTransitionSnapshot
    operation: OperationAccepted

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        if self.transition.reason != "retry":
            raise ValueError("Retry payload requires a retry transition.")
        thread_id = self.transition.thread.view.thread.id
        lineage = self.transition.thread.view.thread.lineage
        if lineage is None or lineage.kind != "retry":
            raise ValueError("Retry transition requires Retry Thread lineage.")
        operation = self.operation
        if operation.thread_id != thread_id or operation.turn_id is None:
            raise ValueError("Retry transition and Operation identities must match.")
        view = self.transition.thread.view
        turn = next(
            (item for item in view.turns if item.id == operation.turn_id),
            None,
        )
        if turn is None or turn.thread_id != thread_id:
            raise ValueError("Retry Operation Turn must belong to the new Thread.")
        in_progress = tuple(
            item for item in view.turns if item.status is TurnStatus.IN_PROGRESS
        )
        if (
            len(in_progress) != 1
            or in_progress[0] != turn
            or not view.turns
            or view.turns[-1] != turn
        ):
            raise ValueError(
                "Retry Operation must identify the final and only in-progress Turn."
            )
        user_entry = next(
            (item for item in view.entries if item.id == turn.user_entry_id),
            None,
        )
        if (
            user_entry is None
            or user_entry.kind is not ThreadEntryKind.USER_MESSAGE
            or user_entry.client_message_id != operation.client_message_id
        ):
            raise ValueError(
                "Retry Operation client identity must match its Turn user Entry."
            )
        return self


class ThreadRenamedPayload(_CommandModel):
    kind: Literal["thread_renamed"] = "thread_renamed"
    thread: Thread


class ThreadExportCommandPayload(_CommandModel):
    kind: Literal["thread_export"] = "thread_export"
    thread_id: str = Field(min_length=1, max_length=128)
    path: str = Field(min_length=1, max_length=1_000)
    format: Literal["markdown", "json"]
    write_status: Literal["created", "updated", "unchanged"]
    byte_count: int = Field(ge=0, le=9_007_199_254_740_991)
    change_set_id: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_change_set(self) -> Self:
        changed = self.write_status != "unchanged"
        if changed != (self.change_set_id is not None):
            raise ValueError("Changed exports require exactly one ChangeSet identity.")
        return self


class ContextCategory(_CommandModel):
    name: Literal["instructions", "conversation", "files", "memory"]
    estimated_tokens: int = Field(ge=0)


class ContextCommandPayload(_CommandModel):
    kind: Literal["context"] = "context"
    categories: tuple[ContextCategory, ...]
    total_tokens: int = Field(ge=0)
    budget_tokens: int = Field(ge=1)


class CompactCommandPayload(_CommandModel):
    kind: Literal["compact"] = "compact"
    old_covered_entry_sequence: int = Field(ge=0)
    new_covered_entry_sequence: int = Field(ge=0)
    usage: UsageSummary


class ModelCommandPayload(_CommandModel):
    kind: Literal["model"] = "model"
    model: str = Field(min_length=1, max_length=200)
    default_model_updated: bool


class ThinkingCommandPayload(_CommandModel):
    kind: Literal["thinking"] = "thinking"
    enabled: bool


class WorkspaceCommandPayload(_CommandModel):
    kind: Literal["workspace"] = "workspace"
    path: str = Field(min_length=1, max_length=4_096)


class DiffCommandPayload(_CommandModel):
    kind: Literal["diff"] = "diff"
    change_set_id: str | None = Field(default=None, max_length=128)
    content: str = Field(default="", max_length=100_000)


class ChangeCommandPayload(_CommandModel):
    kind: Literal["change"] = "change"
    action: Literal["undo", "redo"]
    change_set_id: str = Field(min_length=1, max_length=128)
    lifecycle: str = Field(min_length=1, max_length=64)
    restored_paths: tuple[str, ...] = Field(default=(), max_length=1_000)
    warning: str | None = Field(default=None, max_length=2_000)


class ToolCommandItem(_CommandModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=1_000)
    read_only: bool
    approval_required: bool


class UnavailableToolCommandItem(_CommandModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=1_000)
    read_only: bool
    reason_code: str = Field(pattern=r"^[a-z][a-z0-9_]{0,127}$")
    reason: str = Field(min_length=1, max_length=1_000)
    hint: str = Field(min_length=1, max_length=1_000)


class ToolCatalogCommandPayload(_CommandModel):
    kind: Literal["tools"] = "tools"
    permission_mode: PermissionMode
    tools: tuple[ToolCommandItem, ...]
    unavailable_tools: tuple[UnavailableToolCommandItem, ...]

    @model_validator(mode="after")
    def validate_tool_names(self) -> Self:
        available_names = [tool.name for tool in self.tools]
        unavailable_names = [tool.name for tool in self.unavailable_tools]
        if len(available_names) != len(set(available_names)):
            raise ValueError("Available tool names must be unique.")
        if len(unavailable_names) != len(set(unavailable_names)):
            raise ValueError("Unavailable tool names must be unique.")
        if set(available_names) & set(unavailable_names):
            raise ValueError("Available and unavailable tool names must be disjoint.")
        return self


class SkillCommandItem(_CommandModel):
    name: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=500)
    source: Literal["bundled", "user", "workspace"]


class SkillCommandDiagnostic(_CommandModel):
    code: str = Field(min_length=1, max_length=128)
    name: str | None = Field(default=None, max_length=64)
    source: Literal["bundled", "user", "workspace"]
    message: str = Field(min_length=1, max_length=1_000)


class SkillCatalogCommandPayload(_CommandModel):
    kind: Literal["skills"] = "skills"
    active_mode: str = Field(min_length=1, max_length=64)
    skills: tuple[SkillCommandItem, ...]
    diagnostics: tuple[SkillCommandDiagnostic, ...] = ()


class McpCommandItem(_CommandModel):
    server_id: str = Field(min_length=1, max_length=128)
    state: Literal[
        "disabled",
        "untrusted",
        "enablement_required",
        "configured",
        "connected",
        "error",
    ]
    detail: str | None = Field(default=None, max_length=2_000)


class McpCommandPayload(_CommandModel):
    kind: Literal["mcp"] = "mcp"
    servers: tuple[McpCommandItem, ...]


class MemoryCommandEntry(_CommandModel):
    id: str = Field(min_length=1, max_length=128)
    content: str = Field(min_length=1, max_length=2_000)


class MemorySearchItem(_CommandModel):
    id: str = Field(min_length=1, max_length=128)
    content: str = Field(min_length=1, max_length=500)
    scope: Literal["user", "workspace"]
    fact_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class MemoryStatusCommandPayload(_CommandModel):
    kind: Literal["memory_status"] = "memory_status"
    local_available: bool
    local_enabled: bool
    cloud_provider: Literal["mem0"] = "mem0"
    cloud_available: bool
    cloud_enabled: bool
    cloud_error_code: str | None = Field(default=None, max_length=128)


class MemoryDocumentCommandPayload(_CommandModel):
    kind: Literal["memory_document"] = "memory_document"
    scope: Literal["user", "workspace"]
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    entries: tuple[MemoryCommandEntry, ...]


class MemorySearchCommandPayload(_CommandModel):
    kind: Literal["memory_search"] = "memory_search"
    provider: Literal["mem0"] = "mem0"
    memories: tuple[MemorySearchItem, ...]


class MemoryMutationCommandPayload(_CommandModel):
    kind: Literal["memory_mutation"] = "memory_mutation"
    provider: Literal["local", "mem0"]
    status: str = Field(min_length=1, max_length=64)
    scope: Literal["user", "workspace"] | None = None
    entry_id: str | None = Field(default=None, max_length=128)
    memory_id: str | None = Field(default=None, max_length=128)
    error_code: str | None = Field(default=None, max_length=128)


class StatusCommandPayload(_CommandModel):
    kind: Literal["status"] = "status"
    snapshot: StatusSnapshot


class UsageCommandPayload(_CommandModel):
    kind: Literal["usage"] = "usage"
    usage: UsageSummary


class DoctorCheck(_CommandModel):
    name: str = Field(min_length=1, max_length=128)
    status: Literal["ok", "missing", "valid", "invalid", "unverified", "off", "error"]
    detail: str | None = Field(default=None, max_length=2_000)


class DoctorCommandPayload(_CommandModel):
    kind: Literal["doctor"] = "doctor"
    checks: tuple[DoctorCheck, ...]


class ConfigCommandPayload(_CommandModel):
    kind: Literal["config"] = "config"
    sources: tuple[str, ...]
    credentials: ProviderCredentialStatuses


class PermissionCommandPayload(_CommandModel):
    kind: Literal["permissions"] = "permissions"
    mode: PermissionMode


class WebStatusCommandPayload(_CommandModel):
    kind: Literal["web_status"] = "web_status"
    enabled: bool
    provider: Literal["tavily"] = "tavily"
    available: bool
    credential_configured: bool
    proxy_configured: bool
    thread_authorized: bool
    requests_per_turn: int = Field(ge=0, le=8)
    diagnostic_code: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^[a-z][a-z0-9_]{0,127}$",
    )
    disclosure: str = Field(min_length=1, max_length=2_000)


CommandPayload = Annotated[
    NoticeCommandPayload
    | ThreadTransitionCommandPayload
    | ThreadRetryCommandPayload
    | ThreadRenamedPayload
    | ThreadExportCommandPayload
    | ContextCommandPayload
    | CompactCommandPayload
    | ModelCommandPayload
    | ThinkingCommandPayload
    | WorkspaceCommandPayload
    | DiffCommandPayload
    | ChangeCommandPayload
    | ToolCatalogCommandPayload
    | SkillCatalogCommandPayload
    | McpCommandPayload
    | MemoryStatusCommandPayload
    | MemoryDocumentCommandPayload
    | MemorySearchCommandPayload
    | MemoryMutationCommandPayload
    | StatusCommandPayload
    | UsageCommandPayload
    | DoctorCommandPayload
    | ConfigCommandPayload
    | PermissionCommandPayload
    | WebStatusCommandPayload,
    Field(discriminator="kind"),
]


class CommandError(_CommandModel):
    kind: Literal["error"] = "error"
    code: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=30_000)


class CommandResult(_CommandModel):
    kind: Literal["result"] = "result"
    payload: CommandPayload


class CommandInteractionResult(_CommandModel):
    kind: Literal["interaction"] = "interaction"
    interaction: CommandInteraction
    context: CommandPayload | None = None


CommandOutcome = Annotated[
    CommandResult | CommandInteractionResult | CommandError,
    Field(discriminator="kind"),
]
COMMAND_OUTCOME_ADAPTER: TypeAdapter[CommandOutcome] = TypeAdapter(CommandOutcome)


def result(payload: CommandPayload) -> CommandResult:
    return CommandResult(payload=payload)


def interaction(
    request: CommandInteraction,
    *,
    context: CommandPayload | None = None,
) -> CommandInteractionResult:
    return CommandInteractionResult(interaction=request, context=context)


def error(code: str, message: str) -> CommandError:
    return CommandError(code=code, message=message)
