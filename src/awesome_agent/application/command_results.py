from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from awesome_agent.application.contracts import StatusSnapshot
from awesome_agent.config.credentials import ProviderCredentialStatuses
from awesome_agent.conversation.models import UsageSummary
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
    provider: Literal["deepseek", "kimi", "mem0"]
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


class ThreadCommandPayload(_CommandModel):
    kind: Literal["thread"] = "thread"
    action: Literal["created", "resumed"]
    thread_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=500)


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


class ToolCatalogCommandPayload(_CommandModel):
    kind: Literal["tools"] = "tools"
    permission_mode: PermissionMode
    tools: tuple[ToolCommandItem, ...]


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


CommandPayload = Annotated[
    NoticeCommandPayload
    | ThreadCommandPayload
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
    | PermissionCommandPayload,
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
