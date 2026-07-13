# PR1 Single Command Authority and Typed Contracts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Awesome's duplicated Python command implementations and arbitrary JSON command result with one Application command authority and a Protocol v2 discriminated result contract consumed exactly by the Ink TUI.

**Architecture:** Keep `LocalApplication` in `application/facade.py` as the only surface-facing API and reuse the existing `CommandDispatcher` as the only Application-command router. Extract the reusable conversation and extension services from the obsolete `application/headless.py`, move remaining command semantics out of `composition.py` into cohesive services, and make Python Pydantic models, generated fixtures, TypeScript Zod schemas, the controller, and the exhaustive Presenter agree on one result/interaction/error union.

**Tech Stack:** Python 3.12, Pydantic 2, pytest, JSON-RPC/NDJSON, TypeScript, Zod, React/Ink, Vitest.

## Global Constraints

- Work starts from `codex/tui-command-visual-consistency` and the PR targets that integration branch, not `main`.
- Do not use a worktree or subagent for Inline Execution unless the user changes the execution method.
- Do not change Agent/LangGraph routing, Tool execution, storage schemas, Provider adapters, or user-visible visual styling in this PR.
- Do not add a compatibility parser for the old `status/content/data/selection/secret_prompt` result.
- Increment the private protocol from version 1 to version 2 and delete the v1 fixture corpus; Core and TUI must fail clearly when their protocol versions differ.
- Do not add a returned `progress` outcome. Command progress is a TUI Surface lifecycle while an RPC is pending and is implemented in PR6.
- Do not add a runtime Presenter registry. The built-in result union is handled by a compile-time exhaustive TypeScript switch.
- Do not retain `application/headless.py` after its two live services are extracted; its unused legacy `LocalApplication`, startup types, dispatcher, and duplicate commands are deleted.
- Composition may construct services and register handlers, but it may not branch on `CommandName` or construct user-facing command outcomes.
- Command payloads contain semantic facts only. Borders, colors, labels, aligned columns, glyphs, and spacing stay in the TUI.
- No arbitrary `dict[str, JsonValue]` or `z.record(z.string(), jsonValueSchema)` remains in the `command.execute` success contract.
- No secret value may appear in a command payload, fixture, test assertion output, Presenter, transcript, or log.
- Preserve the current command inventory and ownership: twenty Application commands, one Skill command (`init`), and four Ink commands. The Core dispatcher handles the twenty-one non-Ink commands while ownership continues to describe semantic responsibility.
- Preserve explicit selected credential source behavior: an unavailable selected source returns an error and does not silently fall back.
- Use test-first changes and remove replaced source and implementation-coupled tests in the same task.

---

## Baseline Evidence

Recorded on 2026-07-13 before planning:

```text
uv run pytest tests/unit/application/test_command_contracts.py \
  tests/unit/application/test_dispatcher.py \
  tests/unit/application/test_facade.py \
  tests/unit/protocol/test_contract_fixtures.py -q
42 passed in 6.31s

cd tui
npm test -- --run tests/protocol/contracts.test.ts \
  tests/contracts/fixtures.test.ts \
  tests/commands/controller.test.ts
3 test files, 24 tests passed
```

These checks are the minimum regression baseline. A failing lower gate stops execution before heavier integration checks unless the failure is proven unrelated.

## Target File Responsibilities

### Create

- `src/awesome_agent/application/command_results.py` — all semantic command payloads, input requests, error outcome, discriminated `CommandOutcome`, and constructor helpers.
- `src/awesome_agent/application/conversation_commands.py` — `/new`, `/resume`, `/thinking`, and the selected-Thread authority extracted from `headless.py`.
- `src/awesome_agent/application/extension_commands.py` — `/skills`, `/mcp`, `/memory`, Skill-owned `/init`, and extension preparation extracted from `headless.py`.
- `src/awesome_agent/application/diagnostic_commands.py` — `/workspace`, `/tools`, `/status`, `/usage`, `/doctor`, and `/config` semantics.
- `src/awesome_agent/application/change_commands.py` — `/diff`, `/undo`, and `/redo`, including exact domain error mapping.
- `src/awesome_agent/application/permission_commands.py` — `/permissions` query, downgrade, already-enabled result, and full-access interaction request.
- `protocol/fixtures/v2/command-results.valid.json` — one deterministic Python-produced sample for every payload and interaction variant.
- `protocol/fixtures/v2/command-results.invalid.json` — representative wrong discriminator, missing field, unknown field, nullability, object-array, and secret-field failures.
- `tests/unit/application/test_command_results.py` — Python discriminated union and constructor tests.
- `tests/unit/application/test_command_authority.py` — exact Application handler inventory and one-authority structural checks.
- `tests/unit/application/test_context_commands.py` — four-category Context aggregation and typed Compact outcomes.
- `tui/tests/protocol/command-results.test.ts` — Zod variant, exhaustiveness, and secret-rejection tests.

### Move and modify

- `src/awesome_agent/application/commands.py` — retain only command identity, ownership, and `CommandIntent`; remove result and interaction models.
- `src/awesome_agent/application/dispatcher.py` — dispatch `CommandOutcome`, require the exact non-Ink Core command inventory, and return typed errors.
- `src/awesome_agent/application/context.py` — return typed context/compact payloads and aggregate manifest token categories.
- `src/awesome_agent/application/provider_configuration.py` — return typed interactions, notices, model result, and errors.
- `src/awesome_agent/application/composition.py` — construct/register command services and reduce `run_command()` to one dispatcher call.
- `src/awesome_agent/application/facade.py` — expose `ApplicationResult[CommandOutcome]`.
- `src/awesome_agent/application/__init__.py` — remove obsolete headless startup exports and export only current public Application contracts.
- `src/awesome_agent/protocol/jsonrpc.py` — set `PROTOCOL_VERSION = 2` and return `CommandOutcome`.
- `scripts/generate_protocol_fixtures.py` — generate the complete v2 corpus from Pydantic objects.
- `tests/unit/application/test_command_contracts.py` — retain inventory/intent tests and move result tests to `test_command_results.py`.
- `tests/unit/application/test_dispatcher.py` — assert all and only non-Ink Core commands are registered.
- `tests/unit/application/test_facade.py` — import extracted services and expect typed outcomes.
- `tests/unit/protocol/test_contract_fixtures.py` — load v2 and validate every command outcome with `TypeAdapter`.
- `tests/structural/test_memory_architecture.py` — point visible local-memory command ownership at `extension_commands.py`.
- `tests/structural/test_product_architecture.py` — assert the command service files and single composition importer boundary.
- `tests/integration/test_headless_product.py` — keep the current filename in PR1 to avoid unrelated test-file churn, but assert the composed Application path only; PR8 may rename documentation-facing test terminology.
- `tests/e2e/test_stdio_product.py` and protocol unit tests — use protocol version 2.
- `tui/src/protocol/commands.ts` — exact v2 outcome schemas and exported inferred types.
- `tui/src/protocol/methods.ts` — `command.execute` returns `commandOutcomeSchema`; initialization requires protocol 2.
- `tui/src/protocol/index.ts` — export the new exact schemas/types.
- `tui/src/commands/controller.ts` — route the three outcome kinds without inspecting optional legacy fields.
- `tui/src/commands/presenters.ts` — statically and exhaustively map every payload kind; preserve current minimal appearance until PR4.
- `tui/src/app/App.tsx` — consume controller outcomes by discriminator only; do not perform payload shape discovery.
- TUI protocol/controller tests and fake Core fixtures — use Protocol v2 and exact outcomes.
- `ARCHITECTURE.md`, `docs/architecture/application-and-langgraph.md`, and `docs/architecture/protocol-and-ink.md` — document the one-authority and typed-outcome boundaries.

### Delete

- `src/awesome_agent/application/headless.py` after live services are extracted.
- `protocol/fixtures/v1/` after the complete v2 corpus is generated and both languages load v2.
- Legacy `CommandStatus`, `CommandResult`, `data`, `selection`, and `secret_prompt` schemas and tests.
- Broad `_delegate_command`, `_change_command`, `_permissions_command`, and `_error` command branches from `composition.py`.
- Any Presenter fallback based on `Object.entries`, implicit object conversion, object-array `join`, or `JSON.stringify`.

## Canonical Interfaces

The implementation uses these names consistently in every task.

### Python outcome envelope

`src/awesome_agent/application/command_results.py` defines strict frozen Pydantic models and these aliases:

```python
from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator


class CommandError(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["error"] = "error"
    code: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=30_000)


class CommandResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["result"] = "result"
    payload: CommandPayload


class CommandInteractionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["interaction"] = "interaction"
    interaction: CommandInteraction
    context: CommandPayload | None = None


CommandOutcome = Annotated[
    CommandResult | CommandInteractionResult | CommandError,
    Field(discriminator="kind"),
]
COMMAND_OUTCOME_ADAPTER = TypeAdapter(CommandOutcome)


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
```

Define `CommandPayload` before `CommandResult`, and define `CommandInteraction` before `CommandInteractionResult`, so no runtime forward-reference rebuilding is required.

### Input request union

```python
class CommandOption(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    value: str = Field(min_length=1, max_length=200)
    label: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1_000)
    selected: bool = False
    disabled: bool = False


class CommandSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

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


class CommandSecretPrompt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["secret"] = "secret"
    provider: Literal["deepseek", "kimi", "mem0"]
    action: Literal["add", "replace"]
    label: str = Field(min_length=1, max_length=200)
    environment_variable: str = Field(min_length=1, max_length=128)
    help_url: str = Field(min_length=1, max_length=2_000)


class CommandApplicationInteraction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["application"] = "application"
    interaction_id: str = Field(min_length=1, max_length=128)


CommandInteraction = Annotated[
    CommandSelection | CommandSecretPrompt | CommandApplicationInteraction,
    Field(discriminator="kind"),
]
```

`CommandApplicationInteraction` references an interaction already created by `InteractionCoordinator`; it does not duplicate the approval choices already projected through Application state/events.

### Semantic payload inventory

Every payload is `extra="forbid"`, frozen, and discriminated by `kind`:

| Payload class | `kind` | Exact semantic fields |
| --- | --- | --- |
| `NoticeCommandPayload` | `notice` | `message: str` |
| `ThreadCommandPayload` | `thread` | `action: created|resumed`, `thread_id`, `title` |
| `ContextCommandPayload` | `context` | `categories: tuple[ContextCategory, ...]`, `total_tokens`, `budget_tokens` |
| `CompactCommandPayload` | `compact` | `old_covered_entry_sequence`, `new_covered_entry_sequence`, `usage: UsageSummary` |
| `ModelCommandPayload` | `model` | `model`, `default_model_updated` |
| `ThinkingCommandPayload` | `thinking` | `enabled` |
| `WorkspaceCommandPayload` | `workspace` | `path` |
| `DiffCommandPayload` | `diff` | `change_set_id`, `content` |
| `ChangeCommandPayload` | `change` | `action: undo|redo`, `change_set_id`, `lifecycle`, `restored_paths`, `warning` |
| `ToolCatalogCommandPayload` | `tools` | `permission_mode: PermissionMode`, `tools: tuple[ToolCommandItem, ...]` |
| `SkillCatalogCommandPayload` | `skills` | `active_mode`, `skills`, `diagnostics` |
| `McpCommandPayload` | `mcp` | `servers: tuple[McpCommandItem, ...]` |
| `MemoryStatusCommandPayload` | `memory_status` | `local_available`, `local_enabled`, `cloud_provider: Literal["mem0"]`, `cloud_available`, `cloud_enabled`, `cloud_error_code` |
| `MemoryDocumentCommandPayload` | `memory_document` | `scope`, `content_hash`, `entries` |
| `MemorySearchCommandPayload` | `memory_search` | `provider: Literal["mem0"]`, `memories` |
| `MemoryMutationCommandPayload` | `memory_mutation` | `provider: local|mem0`, `status`, `scope`, `entry_id`, `memory_id`, `error_code` |
| `StatusCommandPayload` | `status` | `snapshot: StatusSnapshot` expanded with credential source availability, Context use/budget, and changed-file count |
| `UsageCommandPayload` | `usage` | `usage: UsageSummary` |
| `DoctorCommandPayload` | `doctor` | `checks: tuple[DoctorCheck, ...]` |
| `ConfigCommandPayload` | `config` | `sources`, `credentials: ProviderCredentialStatuses` |
| `PermissionCommandPayload` | `permissions` | `mode: PermissionMode` |

Supporting item models contain only the currently used semantic fields:

```python
class ContextCategory(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: Literal["instructions", "conversation", "files", "memory"]
    estimated_tokens: int = Field(ge=0)


class ToolCommandItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=1_000)
    read_only: bool
    approval_required: bool


class SkillCommandItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=500)
    source: Literal["bundled", "user", "workspace"]


class SkillCommandDiagnostic(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    code: str = Field(min_length=1, max_length=128)
    name: str | None = Field(default=None, max_length=64)
    source: Literal["bundled", "user", "workspace"]
    message: str = Field(min_length=1, max_length=1_000)


class McpCommandItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
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


class MemoryCommandEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(min_length=1, max_length=128)
    content: str = Field(min_length=1, max_length=2_000)


class MemorySearchItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(min_length=1, max_length=128)
    content: str = Field(min_length=1, max_length=500)
    scope: Literal["user", "workspace"]
    fact_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class DoctorCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = Field(min_length=1, max_length=128)
    status: Literal["ok", "missing", "valid", "invalid", "unverified", "off", "error"]
    detail: str | None = Field(default=None, max_length=2_000)
```

PR1 extends the existing strict `StatusSnapshot` with these semantic facts so
later TUI work never reconstructs them from unrelated Application fields:

```python
credential_source: CredentialSource | None = None
credential_source_available: bool = False
context_used_tokens: int = Field(ge=0)
context_budget_tokens: int = Field(ge=1)
changed_file_count: int = Field(ge=0)
```

`credential_source` is the explicitly selected source for the active model
Provider. `credential_source_available` reports whether that exact source is
currently usable; it does not select a fallback. `context_used_tokens` is the
sum of the latest validated Context manifest estimates, and
`context_budget_tokens` is the configured effective budget.

`CommandPayload` is an `Annotated` union of exactly the payload classes in the table. Do not add a generic mapping, `unknown`, `Any`, or catch-all payload.

### TypeScript outcome envelope

`tui/src/protocol/commands.ts` mirrors the Python contract:

```ts
export const commandPayloadSchema = z.discriminatedUnion("kind", [
  noticeCommandPayloadSchema,
  threadCommandPayloadSchema,
  contextCommandPayloadSchema,
  compactCommandPayloadSchema,
  modelCommandPayloadSchema,
  thinkingCommandPayloadSchema,
  workspaceCommandPayloadSchema,
  diffCommandPayloadSchema,
  changeCommandPayloadSchema,
  toolCatalogCommandPayloadSchema,
  skillCatalogCommandPayloadSchema,
  mcpCommandPayloadSchema,
  memoryStatusCommandPayloadSchema,
  memoryDocumentCommandPayloadSchema,
  memorySearchCommandPayloadSchema,
  memoryMutationCommandPayloadSchema,
  statusCommandPayloadSchema,
  usageCommandPayloadSchema,
  doctorCommandPayloadSchema,
  configCommandPayloadSchema,
  permissionCommandPayloadSchema,
]);

export const commandInteractionSchema = z.discriminatedUnion("kind", [
  commandSelectionSchema,
  commandSecretPromptSchema,
  commandApplicationInteractionSchema,
]);

export const commandOutcomeSchema = z.discriminatedUnion("kind", [
  z.strictObject({ kind: z.literal("result"), payload: commandPayloadSchema }),
  z.strictObject({
    kind: z.literal("interaction"),
    interaction: commandInteractionSchema,
    context: commandPayloadSchema.optional(),
  }),
  z.strictObject({
    kind: z.literal("error"),
    code: boundedText(1, 128),
    message: boundedText(1, 30_000),
  }),
]);

export type CommandPayload = z.infer<typeof commandPayloadSchema>;
export type CommandOutcome = z.infer<typeof commandOutcomeSchema>;
```

No TypeScript command schema imports `jsonValueSchema` after this migration.

### Dispatcher inventory

`CommandDispatcher` is constructed with a complete immutable handler mapping rather than being partially usable:

```python
type CommandHandler = Callable[[CommandIntent], Awaitable[CommandOutcome]]


class CommandDispatcher:
    def __init__(self, handlers: Mapping[CommandName, CommandHandler]) -> None:
        core_names = {
            name
            for name, owner in COMMAND_OWNERS.items()
            if owner is not CommandOwner.INK
        }
        registered = set(handlers)
        if registered != core_names:
            raise InvalidCommandInventory(
                missing=core_names - registered,
                unexpected=registered - core_names,
            )
        self._handlers = dict(handlers)

    @property
    def registered_names(self) -> tuple[CommandName, ...]:
        return tuple(sorted(self._handlers, key=lambda name: name.value))

    async def dispatch(self, intent: CommandIntent) -> CommandOutcome:
        if COMMAND_OWNERS[intent.name] is CommandOwner.INK:
            return error("surface_command", "Command is owned by the interactive surface.")
        return await self._handlers[intent.name](intent)
```

This removes partially registered production dispatchers and makes missing handlers a startup/test failure rather than a runtime user failure.

## Task 0: Prepare the PR1 Branch and Execution Record

**Files:**
- Create during execution: `.codex/pr-bodies/pr1-typed-command-authority.md`
- Modify during execution: `docs/superpowers/plans/2026-07-13-pr1-single-command-authority-and-typed-contracts.md` checkboxes and evidence only

**Interfaces:**
- Consumes: clean `codex/tui-command-visual-consistency` at commit `2769afe2` or its fast-forward successor containing this plan.
- Produces: branch `codex/pr1-typed-command-authority` with an explainable clean baseline.

- [ ] **Step 1: Confirm the integration baseline**

```powershell
git switch codex/tui-command-visual-consistency
git pull --ff-only
git status --short --branch
```

Expected: the integration branch is current and the status contains no unexplained production changes.

- [ ] **Step 2: Create the PR1 branch without a worktree**

```powershell
git switch -c codex/pr1-typed-command-authority
git status --short --branch
```

Expected: current branch is `codex/pr1-typed-command-authority`.

- [ ] **Step 3: Record baseline verification in this plan**

Run the Baseline Evidence commands again. Mark their results under the corresponding checklist without changing acceptance criteria or adding skips.

## Task 1: Freeze the Python Outcome Contract

**Files:**
- Create: `src/awesome_agent/application/command_results.py`
- Create: `tests/unit/application/test_command_results.py`
- Modify: `src/awesome_agent/application/commands.py`
- Modify: `tests/unit/application/test_command_contracts.py`

**Interfaces:**
- Consumes: existing `CommandName`, `PermissionMode`, `StatusSnapshot`, `UsageSummary`, and `ProviderCredentialStatuses`.
- Produces: `CommandOutcome`, `CommandPayload`, `CommandInteraction`, `result()`, `interaction()`, `error()`, and every canonical payload class listed above.

- [ ] **Step 1: Add failing discriminated-union tests**

Add tests that construct one instance of every payload, wrap it in `result()`, serialize with `exclude_none=True`, and parse it with `COMMAND_OUTCOME_ADAPTER`. Add explicit rejection tests for legacy `status/content/data`, simultaneous interaction variants, unknown fields, missing discriminators, a null non-nullable field, duplicate Picker values, two selected Picker options, and an injected `api_key` field.

```python
def test_legacy_arbitrary_command_result_is_rejected() -> None:
    with pytest.raises(ValidationError):
        COMMAND_OUTCOME_ADAPTER.validate_python(
            {"status": "success", "content": "", "data": {}}
        )


def test_secret_interaction_serializes_metadata_only() -> None:
    outcome = interaction(
        CommandSecretPrompt(
            provider="deepseek",
            action="add",
            label="DeepSeek API Key",
            environment_variable="DEEPSEEK_API_KEY",
            help_url="https://platform.deepseek.com/api_keys",
        )
    )
    serialized = outcome.model_dump(mode="json", exclude_none=True)
    assert "api_key" not in str(serialized)
    assert COMMAND_OUTCOME_ADAPTER.validate_python(serialized) == outcome
```

- [ ] **Step 2: Run the contract tests and verify the new module is absent**

Run:

```powershell
uv run pytest tests/unit/application/test_command_results.py tests/unit/application/test_command_contracts.py -q
```

Expected: FAIL during collection because `awesome_agent.application.command_results` does not exist.

- [ ] **Step 3: Implement the exact canonical interfaces**

Create `command_results.py` from the canonical interfaces above. Move `CommandOption`, `CommandSelection`, and `CommandSecretPrompt` out of `commands.py` and use the exact `validate_options()` implementation shown above. Leave only `CommandOwner`, `CommandName`, `COMMAND_OWNERS`, and `CommandIntent` in `commands.py`.

- [ ] **Step 4: Run the focused Python contract tests**

Run:

```powershell
uv run pytest tests/unit/application/test_command_results.py tests/unit/application/test_command_contracts.py -q
```

Expected: PASS with no legacy result accepted.

- [ ] **Step 5: Commit the Python contract**

```powershell
git add src/awesome_agent/application/commands.py src/awesome_agent/application/command_results.py tests/unit/application/test_command_contracts.py tests/unit/application/test_command_results.py
git commit -m "refactor: define typed command outcomes"
```

## Task 2: Establish Protocol v2 and Cross-language Fixtures

**Files:**
- Modify: `src/awesome_agent/protocol/jsonrpc.py`
- Modify: `src/awesome_agent/application/contracts.py`
- Modify: `src/awesome_agent/application/composition.py` only for protocol literals in initialization
- Modify: `scripts/generate_protocol_fixtures.py`
- Create: `protocol/fixtures/v2/*`
- Delete: `protocol/fixtures/v1/*`
- Modify: `tests/unit/protocol/test_contract_fixtures.py`
- Modify: all Python tests with literal protocol version 1
- Modify: `tui/src/protocol/commands.ts`
- Modify: `tui/src/protocol/methods.ts`
- Modify: `tui/src/protocol/index.ts`
- Modify: `tui/src/surface/startup.ts`
- Modify: `tui/tests/contracts/fixture-loader.ts`
- Create: `tui/tests/protocol/command-results.test.ts`
- Modify: all TUI tests/fakes with literal protocol version 1

**Interfaces:**
- Consumes: Task 1 `CommandOutcome` and adapter.
- Produces: Protocol version 2, generated v2 fixtures, `commandOutcomeSchema`, and inferred TypeScript types.

- [ ] **Step 1: Add failing Protocol v2 and corpus-completeness tests**

Python assertions:

```python
assert PROTOCOL_VERSION == 2
assert manifest["protocol_version"] == 2
assert set(manifest["files"]) >= {
    "command-results.valid.json",
    "command-results.invalid.json",
}
for case in _cases("command-results.valid.json"):
    COMMAND_OUTCOME_ADAPTER.validate_python(case["outcome"])
for case in _cases("command-results.invalid.json"):
    with pytest.raises(ValidationError):
        COMMAND_OUTCOME_ADAPTER.validate_python(case["outcome"])
```

TypeScript assertions:

```ts
for (const fixture of cases(corpus.files["command-results.valid.json"])) {
  expect(commandOutcomeSchema.safeParse(fixture.outcome).success, fixture.name).toBe(true);
}
for (const fixture of cases(corpus.files["command-results.invalid.json"])) {
  expect(commandOutcomeSchema.safeParse(fixture.outcome).success, fixture.name).toBe(false);
}
```

- [ ] **Step 2: Run both fixture suites and verify they fail on version/corpus**

Run:

```powershell
uv run pytest tests/unit/protocol/test_contract_fixtures.py -q
Push-Location tui
npm test -- --run tests/contracts/fixtures.test.ts tests/protocol/command-results.test.ts
Pop-Location
```

Expected: Python and TypeScript fail because v2 fixtures and schemas do not exist and current protocol literals equal 1.

- [ ] **Step 3: Implement exact Zod mirrors and Protocol v2 constants**

Implement the TypeScript union shown under Canonical Interfaces. Change initialization literals and result schemas to 2. Change the fixture generator target to `protocol/fixtures/v2`, generate one valid fixture for every payload and interaction variant, and generate the declared invalid cases from plain dictionaries that Pydantic must reject.

The valid `command.execute` method fixture uses:

```python
_success(result(StatusCommandPayload(snapshot=status_snapshot)))
```

The incompatible initialize fixture sends protocol version 1 and expects `protocol_version_incompatible`; version 2 is the only accepted value.

- [ ] **Step 4: Generate v2 fixtures and remove v1**

Run:

```powershell
uv run python scripts/generate_protocol_fixtures.py
Remove-Item -Recurse -Force -LiteralPath (Resolve-Path 'protocol/fixtures/v1')
```

Before `Remove-Item`, verify `Resolve-Path 'protocol/fixtures/v1'` starts with `E:\awesome_agent\protocol\fixtures\v1`. Expected: only `protocol/fixtures/v2` remains.

- [ ] **Step 5: Run producer-consumer contract tests**

Run:

```powershell
uv run python scripts/generate_protocol_fixtures.py --check
uv run pytest tests/unit/protocol/test_contract_fixtures.py tests/unit/protocol/test_jsonrpc.py tests/unit/protocol/test_stdio.py -q
Push-Location tui
npm test -- --run tests/contracts/fixtures.test.ts tests/protocol/command-results.test.ts tests/protocol/contracts.test.ts tests/surface/startup.test.ts
npm run typecheck
Pop-Location
```

Expected: PASS; TypeScript rejects every invalid Python fixture and accepts every valid variant.

- [ ] **Step 6: Commit Protocol v2**

```powershell
git add src/awesome_agent/protocol src/awesome_agent/application/contracts.py src/awesome_agent/application/composition.py scripts/generate_protocol_fixtures.py protocol tests/unit/protocol tests/e2e/test_stdio_product.py tui/src/protocol tui/src/surface/startup.ts tui/tests
git commit -m "refactor: introduce typed command protocol v2"
```

## Task 3: Extract Live Services and Delete the Legacy Headless Host

**Files:**
- Create: `src/awesome_agent/application/conversation_commands.py`
- Create: `src/awesome_agent/application/extension_commands.py`
- Delete: `src/awesome_agent/application/headless.py`
- Modify: `src/awesome_agent/application/__init__.py`
- Modify: `src/awesome_agent/application/composition.py`
- Modify: `tests/unit/application/test_facade.py`
- Modify: `tests/integration/test_local_memory.py`
- Modify: `tests/integration/test_mem0_cloud.py`
- Modify: `tests/integration/test_skills_mcp.py`
- Modify: `tests/structural/test_memory_architecture.py`

**Interfaces:**
- Consumes: Task 1 outcomes and existing Conversation, Skills, MCP, and Memory services.
- Produces: focused `ConversationCommandService` and `ApplicationExtensionService`; no `headless.py` product host.

- [ ] **Step 1: Add structural and behavior tests for the extracted ownership**

```python
def test_legacy_headless_host_is_removed() -> None:
    assert not Path("src/awesome_agent/application/headless.py").exists()


def test_live_command_services_have_focused_modules() -> None:
    assert Path("src/awesome_agent/application/conversation_commands.py").is_file()
    assert Path("src/awesome_agent/application/extension_commands.py").is_file()
```

Update imports in service tests to their target modules before moving code, then run them to prove they fail during collection.

- [ ] **Step 2: Run focused extraction tests and verify failure**

Run:

```powershell
uv run pytest tests/unit/application/test_facade.py tests/integration/test_local_memory.py tests/integration/test_mem0_cloud.py tests/integration/test_skills_mcp.py tests/structural/test_memory_architecture.py -q
```

Expected: FAIL because the target modules do not exist and `headless.py` still exists.

- [ ] **Step 3: Move the conversation service without its delegate router**

Move `ConversationCommandService` into `conversation_commands.py`. Replace `handle()` with the three explicit public async methods `new(intent) -> CommandOutcome`, `resume(intent) -> CommandOutcome`, and `thinking(intent) -> CommandOutcome` so the dispatcher owns routing.

Keep `current_thread_id` as the sole selected-Thread authority and preserve permission reset on create/resume. Convert returns to `ThreadCommandPayload`, `ThinkingCommandPayload`, selection interaction, notice, or typed error.

- [ ] **Step 4: Move the extension service and preserve Skill-owned `/init` handling**

Move `ApplicationExtensionService` into `extension_commands.py` and expose the public async methods `skills(intent) -> CommandOutcome`, `mcp(intent) -> CommandOutcome`, `memory(intent) -> CommandOutcome`, `init(intent) -> CommandOutcome`, and `prepare_turn_extensions() -> None`.

Inject `current_thread_id: Callable[[], str | None]` instead of accepting `thread_id` through a delegate. Return `thread_not_found` when Skills or `/init` require a Thread and none is selected. Preserve `TurnSubmitter` and the current `/init` behavior: resolve the bundled `init` Skill, select it for the Thread, submit the initialization Agent Turn, and return a typed notice result. Remove only the generic `_SKILL_COMMANDS` router after exposing the explicit `init()` method.

- [ ] **Step 5: Delete obsolete host code and startup exports**

Delete `headless.py`. Remove `StartupResult` and `StartupStatus` imports/exports from `application/__init__.py`. Update all remaining service imports. Do not copy the legacy `LocalApplication` class or its `_command_*` methods anywhere.

- [ ] **Step 6: Run extraction, structural, and import tests**

Run:

```powershell
uv run pytest tests/unit/application/test_facade.py tests/integration/test_local_memory.py tests/integration/test_mem0_cloud.py tests/integration/test_skills_mcp.py tests/structural/test_memory_architecture.py tests/structural/test_product_architecture.py -q
uv run python -c "import awesome_agent.application; import awesome_agent.application.composition"
```

Expected: PASS and no import resolves `awesome_agent.application.headless`.

- [ ] **Step 7: Commit the extraction and deletion**

```powershell
git add src/awesome_agent/application tests/unit/application/test_facade.py tests/integration/test_local_memory.py tests/integration/test_mem0_cloud.py tests/integration/test_skills_mcp.py tests/structural/test_memory_architecture.py tests/structural/test_product_architecture.py
git commit -m "refactor: remove duplicate headless application"
```

## Task 4: Move Remaining Command Semantics out of Composition

**Files:**
- Create: `src/awesome_agent/application/diagnostic_commands.py`
- Create: `src/awesome_agent/application/change_commands.py`
- Create: `src/awesome_agent/application/permission_commands.py`
- Create: `tests/unit/application/test_diagnostic_commands.py`
- Create: `tests/unit/application/test_change_commands.py`
- Create: `tests/unit/application/test_permission_commands.py`
- Modify: `tests/unit/application/test_status.py`
- Modify: `tests/integration/test_headless_product.py`

**Interfaces:**
- Consumes: Task 1 outcome helpers, existing repositories/services, and `current_thread_id: Callable[[], str | None]`.
- Produces: focused handler methods registered in Task 5.

- [ ] **Step 1: Write failing service tests for exact outcomes**

Cover:

```python
assert await diagnostics.workspace(CommandIntent(name=CommandName.WORKSPACE)) == result(
    WorkspaceCommandPayload(path=str(workspace.display_path))
)
assert (await changes.undo(CommandIntent(name=CommandName.UNDO))).kind == "error"
assert (await permissions.permissions(CommandIntent(name=CommandName.PERMISSIONS))).kind == "interaction"
```

Also assert Tool items remain dynamic and carry `approval_required` computed by the current Tool policy and permission mode; Status wraps the expanded `StatusSnapshot`; Usage wraps `UsageSummary`; Doctor emits explicit checks; Config contains `ProviderCredentialStatuses`; Diff has an explicit empty error; and every Change exception maps to its exact code.

- [ ] **Step 2: Run new service tests and verify missing modules**

Run:

```powershell
uv run pytest tests/unit/application/test_diagnostic_commands.py tests/unit/application/test_change_commands.py tests/unit/application/test_permission_commands.py -q
```

Expected: FAIL during collection because the service modules do not exist.

- [ ] **Step 3: Implement `DiagnosticCommandService`**

Expose one public async method per registered command: `workspace(intent)`, `tools(intent)`, `status(intent)`, `usage(intent)`, `doctor(intent)`, and `config(intent)`, each returning `CommandOutcome`.

Move the existing semantic reads from composition. `/workspace` accepts no revoke argument and returns only the normalized display path. Build `DoctorCheck` rows for configuration, SQLite, checkpoints, DeepSeek, and Kimi rather than returning a nested provider dictionary.

- [ ] **Step 4: Implement `ChangeCommandService` with exact error mapping**

Expose `diff`, `undo`, and `redo`. Map only these domain exceptions:

```python
except ChangeSetNotFound:
    return error("change_set_not_found", "ChangeSet was not found.")
except ChangeConflict:
    return error("workspace_conflict", "Workspace content conflicts with the recorded change.")
except ChangeNotReversible:
    return error("change_not_reversible", "ChangeSet is not reversible.")
except ChangeLifecycleError:
    return error("invalid_change_lifecycle", "ChangeSet is not in the required lifecycle.")
```

Do not catch `Exception`; unexpected failures must reach the protocol fatal boundary. Return `DiffCommandPayload` or `ChangeCommandPayload` on success.

- [ ] **Step 5: Implement `PermissionCommandService`**

Expose `permissions()`. Query returns `PermissionCommandPayload`. Switching to request approval resets the session and returns the new mode. Enabling already-enabled full access returns the same typed mode. A new escalation creates the existing `InteractionCoordinator` pending request and returns:

```python
interaction(CommandApplicationInteraction(interaction_id=pending.id))
```

The service does not duplicate approval choices in the command payload.

- [ ] **Step 6: Run focused command service tests**

Run:

```powershell
uv run pytest tests/unit/application/test_diagnostic_commands.py tests/unit/application/test_change_commands.py tests/unit/application/test_permission_commands.py tests/unit/application/test_status.py tests/integration/test_headless_product.py -q
```

Expected: PASS with exact error codes and no broad exception conversion.

- [ ] **Step 7: Commit focused services**

```powershell
git add src/awesome_agent/application/diagnostic_commands.py src/awesome_agent/application/change_commands.py src/awesome_agent/application/permission_commands.py tests/unit/application/test_diagnostic_commands.py tests/unit/application/test_change_commands.py tests/unit/application/test_permission_commands.py tests/unit/application/test_status.py tests/integration/test_headless_product.py
git commit -m "refactor: isolate application command services"
```

## Task 5: Wire One Complete Dispatcher in Composition

**Files:**
- Modify: `src/awesome_agent/application/dispatcher.py`
- Modify: `src/awesome_agent/application/composition.py`
- Modify: `src/awesome_agent/application/facade.py`
- Modify: `tests/unit/application/test_dispatcher.py`
- Create: `tests/unit/application/test_command_authority.py`
- Modify: `tests/structural/test_product_architecture.py`

**Interfaces:**
- Consumes: Tasks 3 and 4 service methods and Task 1 `CommandOutcome`.
- Produces: one complete `CommandDispatcher` stored by `_LocalApplicationBackend`; `run_command()` delegates only to it.

- [ ] **Step 1: Write failing exact-inventory and no-branch tests**

```python
def test_composition_wires_but_does_not_implement_commands() -> None:
    source = Path("src/awesome_agent/application/composition.py").read_text("utf-8")
    run_command = source.split("async def run_command", 1)[1].split("async def", 1)[0]
    assert "CommandName." not in run_command
    assert "CommandResult(" not in source
    assert "CommandError(" not in source


def test_dispatcher_requires_every_core_handler() -> None:
    with pytest.raises(InvalidCommandInventory):
        CommandDispatcher({})
```

- [ ] **Step 2: Run dispatcher and authority tests and verify failure**

Run:

```powershell
uv run pytest tests/unit/application/test_dispatcher.py tests/unit/application/test_command_authority.py tests/structural/test_product_architecture.py -q
```

Expected: FAIL because the current dispatcher is partially registered and composition implements commands.

- [ ] **Step 3: Implement complete immutable dispatcher construction**

Use the canonical dispatcher interface. In `_activate()`, construct all services and one mapping containing exactly the twenty-one non-Ink Core command names:

```python
self._command_dispatcher = CommandDispatcher(
    {
        CommandName.NEW: self._conversation_commands.new,
        CommandName.RESUME: self._conversation_commands.resume,
        CommandName.CONTEXT: self._context_command,
        CommandName.COMPACT: self._compact_command,
        CommandName.AUTH: self._provider_configuration.auth_command,
        CommandName.MODEL: self._model_command,
        CommandName.THINKING: self._conversation_commands.thinking,
        CommandName.WORKSPACE: self._diagnostic_commands.workspace,
        CommandName.DIFF: self._change_commands.diff,
        CommandName.UNDO: self._change_commands.undo,
        CommandName.REDO: self._change_commands.redo,
        CommandName.TOOLS: self._diagnostic_commands.tools,
        CommandName.SKILLS: self._extension_commands.skills,
        CommandName.MCP: self._extension_commands.mcp,
        CommandName.MEMORY: self._extension_commands.memory,
        CommandName.STATUS: self._diagnostic_commands.status,
        CommandName.USAGE: self._diagnostic_commands.usage,
        CommandName.DOCTOR: self._diagnostic_commands.doctor,
        CommandName.CONFIG: self._diagnostic_commands.config,
        CommandName.PERMISSIONS: self._permission_commands.permissions,
        CommandName.INIT: self._extension_commands.init,
    }
)
```

The private `_context_command`, `_compact_command`, and `_model_command` adapters may resolve the selected Thread and call their domain service; they must contain no presentation formatting and return a typed `thread_not_found` error when required.

- [ ] **Step 4: Reduce `run_command()` and delete replaced branches**

```python
async def run_command(self, intent: CommandIntent) -> CommandOutcome:
    self._require_active()
    assert self._command_dispatcher is not None
    return await self._command_dispatcher.dispatch(intent)
```

Delete `_delegate_command`, `_permissions_command`, `_change_command`, the command `_error` helper, and all command-specific `if intent.name` branches from composition. Keep composition-only dependency construction and lifecycle logic.

- [ ] **Step 5: Update facade typing and run authority tests**

Change `ApplicationFacade.execute_command`, `_ApplicationBackend.run_command`, and `LocalApplication.execute_command` to `ApplicationResult[CommandOutcome]`. Run:

```powershell
uv run pytest tests/unit/application/test_dispatcher.py tests/unit/application/test_command_authority.py tests/unit/application/test_facade.py tests/structural/test_product_architecture.py -q
```

Expected: PASS and the registered inventory exactly equals all non-Ink commands; Ink commands are rejected before RPC by the TUI and by the dispatcher invariant.

- [ ] **Step 6: Commit the single authority**

```powershell
git add src/awesome_agent/application/dispatcher.py src/awesome_agent/application/composition.py src/awesome_agent/application/facade.py tests/unit/application/test_dispatcher.py tests/unit/application/test_command_authority.py tests/unit/application/test_facade.py tests/structural/test_product_architecture.py
git commit -m "refactor: centralize application command dispatch"
```

## Task 6: Convert Context, Provider, Extension, and Conversation Outcomes

**Files:**
- Modify: `src/awesome_agent/application/context.py`
- Modify: `src/awesome_agent/application/provider_configuration.py`
- Modify: `src/awesome_agent/application/conversation_commands.py`
- Modify: `src/awesome_agent/application/extension_commands.py`
- Create: `tests/unit/application/test_context_commands.py`
- Modify: `tests/unit/application/test_provider_configuration.py`
- Modify: `tests/unit/application/test_facade.py`
- Modify: `tests/integration/test_local_memory.py`
- Modify: `tests/integration/test_mem0_cloud.py`
- Modify: `tests/integration/test_skills_mcp.py`

**Interfaces:**
- Consumes: Task 1 payload and interaction classes and Task 5 dispatcher adapters.
- Produces: no remaining legacy result construction in any Application command service.

- [ ] **Step 1: Add failing exact-payload tests**

For Context, build a manifest containing every `ContextSourceKind` and assert exact aggregation:

```python
assert payload.categories == (
    ContextCategory(name="instructions", estimated_tokens=40),
    ContextCategory(name="conversation", estimated_tokens=80),
    ContextCategory(name="files", estimated_tokens=20),
    ContextCategory(name="memory", estimated_tokens=10),
)
assert payload.total_tokens == 150
assert payload.budget_tokens == 262_144
```

Provider tests assert selection interactions, secret interactions, typed model result, notice result, and `selected_credential_unavailable` without a source change. Extension tests assert typed Skills context plus Picker, MCP items, Memory status/document/search/mutation, and errors.

- [ ] **Step 2: Run focused tests and verify legacy result failures**

Run:

```powershell
uv run pytest tests/unit/application/test_context_commands.py tests/unit/application/test_provider_configuration.py tests/unit/application/test_facade.py tests/integration/test_local_memory.py tests/integration/test_mem0_cloud.py tests/integration/test_skills_mcp.py -q
```

Expected: `test_context_commands.py` fails on the new typed assertions until Context aggregation is implemented; existing Provider tests fail after their expectations are changed to the new outcome union.

- [ ] **Step 3: Aggregate Context into four semantic categories**

Use this fixed mapping:

```python
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
```

Validate stored manifest dictionaries through `ContextManifestItem` before aggregation. Invalid stored manifest data remains an invariant failure rather than being stringified. Return categories in the fixed instructions/conversation/files/memory order, including zero values, and use `_configured_total_tokens` as `budget_tokens`.

- [ ] **Step 4: Convert all remaining command methods using the mapping table**

| Existing result | Replacement |
| --- | --- |
| content-only success | `result(NoticeCommandPayload(message=message))` |
| error status + `error_code` | `error(code, message)` |
| `selection=` | `interaction(selection_request, context=context_payload)` |
| `secret_prompt=` | `interaction(secret_request)` |
| new/resume dictionary | `ThreadCommandPayload` |
| thinking dictionary | `ThinkingCommandPayload` |
| compact dictionary | `CompactCommandPayload` |
| model dictionary | `ModelCommandPayload` |
| skills dictionary + selection | `SkillCatalogCommandPayload` as interaction context |
| MCP dictionary | `McpCommandPayload` |
| memory dictionaries | the matching typed Memory payload |

Do not add a conversion helper that accepts arbitrary dictionaries.

- [ ] **Step 5: Prove no legacy construction remains**

Run:

```powershell
rg -n "CommandStatus|CommandResult\(|data=|selection=|secret_prompt=" src/awesome_agent/application
```

Expected: no matches for the removed API. A `selected=` option flag is allowed; the exact `selection=` constructor keyword is not.

- [ ] **Step 6: Run all affected Python command tests**

Run:

```powershell
uv run pytest tests/unit/application tests/unit/protocol/test_contract_fixtures.py tests/integration/test_local_memory.py tests/integration/test_mem0_cloud.py tests/integration/test_skills_mcp.py tests/integration/test_headless_product.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit typed handlers**

```powershell
git add src/awesome_agent/application tests/unit/application tests/unit/protocol/test_contract_fixtures.py tests/integration/test_local_memory.py tests/integration/test_mem0_cloud.py tests/integration/test_skills_mcp.py tests/integration/test_headless_product.py
git commit -m "refactor: return semantic command payloads"
```

## Task 7: Consume Typed Outcomes in the TUI Without a Fallback

**Files:**
- Modify: `tui/src/commands/controller.ts`
- Modify: `tui/src/commands/presenters.ts`
- Modify: `tui/src/app/App.tsx`
- Modify: `tui/tests/commands/controller.test.ts`
- Modify: `tui/tests/commands/presenters.test.tsx`
- Modify: `tui/tests/protocol/contracts.test.ts`

**Interfaces:**
- Consumes: Task 2 `CommandOutcome` and `CommandPayload`.
- Produces: controller outcomes `result`, `selection`, `secret`, `application_interaction`, and `error`; exhaustive semantic presentation with no generic JSON path.

- [ ] **Step 1: Write failing controller and exhaustiveness tests**

Controller expectations:

```ts
expect(await controller.submit(statusCommand, threadId)).toEqual({
  kind: "result",
  payload: statusPayload,
});
expect(await controller.submit(authCommand, threadId)).toEqual({
  kind: "selection",
  selection,
  context: undefined,
});
expect(await controller.submit(badCommand, threadId)).toEqual({
  kind: "command_error",
  code: "invalid_arguments",
  message: "Usage: /status",
});
```

Add a `never` exhaustiveness helper:

```ts
function assertNever(value: never): never {
  throw new Error(`Unhandled command payload: ${String(value)}`);
}
```

No test may accept serialized length, non-empty JSON, or `[object Object]` as evidence.

- [ ] **Step 2: Run focused TUI tests and verify schema/controller failure**

Run:

```powershell
Push-Location tui
npm test -- --run tests/commands/controller.test.ts tests/commands/presenters.test.tsx tests/protocol/contracts.test.ts
Pop-Location
```

Expected: FAIL because controller and Presenter still inspect legacy optional fields and `data`.

- [ ] **Step 3: Route exact outcome discriminators in the controller**

Map:

```ts
switch (outcome.kind) {
  case "result":
    return { kind: "result", payload: outcome.payload };
  case "error":
    return { kind: "command_error", code: outcome.code, message: outcome.message };
  case "interaction":
    switch (outcome.interaction.kind) {
      case "selection":
        return { kind: "selection", selection: outcome.interaction, context: outcome.context ?? undefined };
      case "secret":
        return { kind: "secret", prompt: outcome.interaction };
      case "application":
        return { kind: "application_interaction", interactionId: outcome.interaction.interaction_id };
    }
}
```

The controller never checks `status`, `content`, `data`, `selection`, or `secret_prompt`.

- [ ] **Step 4: Replace the generic Presenter with an exhaustive semantic mapping**

Use `switch (payload.kind)` and return the existing `CommandPresentation` primitives. Every payload kind gets a case. For PR1, preserve minimal current text/rows and defer final borders/alignment/copy to PR4, but do not use a fallback formatter. The `default` branch calls `assertNever(payload)`.

Specific safety assertions:

- Context reads `categories`, never the raw manifest.
- Tools iterate typed Tool items one per semantic row.
- Skills/MCP/Memory iterate typed arrays.
- Status reads `payload.snapshot`.
- Doctor iterates typed checks.
- Objects are never passed to `String`, `.join`, `Object.entries`, or `JSON.stringify`.

- [ ] **Step 5: Update `App.tsx` to consume controller discriminators only**

Replace command-outcome optional-field checks with one switch. Keep existing mode opening, state refresh, and transcript append behavior; PR2 will relocate command user blocks and status into the final transcript model. Do not perform PR2 visual or Thread replacement work here.

- [ ] **Step 6: Prove fallback removal and run TUI checks**

Run:

```powershell
rg -n "Object\.entries|JSON\.stringify|\.join\(|\[object Object\]|result\.data|result\.content|result\.selection|result\.secret_prompt" tui/src/commands tui/src/app/App.tsx
npm --prefix tui run format:check
npm --prefix tui run lint
npm --prefix tui run typecheck
npm --prefix tui test -- --run tests/commands/controller.test.ts tests/commands/presenters.test.tsx tests/protocol/contracts.test.ts tests/contracts/fixtures.test.ts
```

Expected: the forbidden result/fallback patterns have no matches in command presentation/controller code; checks PASS. Legitimate `.join()` outside command-object formatting is reviewed rather than deleted mechanically.

- [ ] **Step 7: Commit the typed TUI consumer**

```powershell
git add tui/src tui/tests
git commit -m "refactor: consume typed command outcomes"
```

## Task 8: Architecture Documentation and PR1 Verification

**Files:**
- Modify: `ARCHITECTURE.md`
- Modify: `docs/architecture/application-and-langgraph.md`
- Modify: `docs/architecture/protocol-and-ink.md`
- Modify: `docs/superpowers/plans/2026-07-13-pr1-single-command-authority-and-typed-contracts.md` only to record execution evidence and checked boxes

**Interfaces:**
- Consumes: the completed PR1 architecture.
- Produces: current documentation and the final verification record used by the PR.

- [ ] **Step 1: Update architecture boundaries and diagrams**

Document this exact dependency chain:

```text
Ink command controller
  -> Protocol v2 command.execute
  -> LocalApplication facade
  -> complete CommandDispatcher
  -> one focused command service
  -> CommandOutcome
  -> exhaustive TUI Presenter
  -> current transcript path
```

State that composition wires dependencies, no alternate headless host exists, Progress is a Surface pending lifecycle, and Protocol v1 is intentionally unsupported.

- [ ] **Step 2: Run Python formatting, lint, and type checks**

Run:

```powershell
uv run ruff format --check src tests scripts
uv run ruff check src tests scripts
uv run mypy
```

Expected: PASS.

- [ ] **Step 3: Run TypeScript formatting, lint, type, and build checks**

Run:

```powershell
npm --prefix tui run format:check
npm --prefix tui run lint
npm --prefix tui run typecheck
npm --prefix tui run build
```

Expected: PASS.

- [ ] **Step 4: Run the PR1 affected Python suite**

Run:

```powershell
uv run pytest tests/unit/application tests/unit/protocol tests/structural/test_application_architecture.py tests/structural/test_memory_architecture.py tests/structural/test_product_architecture.py tests/integration/test_local_memory.py tests/integration/test_mem0_cloud.py tests/integration/test_skills_mcp.py tests/integration/test_headless_product.py tests/e2e/test_stdio_product.py -q
```

Expected: PASS. Do not run unrelated full performance, external-provider, installer, or cross-host suites for this architecture PR.

- [ ] **Step 5: Run the PR1 affected TUI suite**

Run:

```powershell
npm --prefix tui test -- --run tests/protocol tests/contracts tests/commands tests/surface/startup.test.ts tests/e2e/stdio-purity.test.ts
```

Expected: PASS.

- [ ] **Step 6: Run architecture deletion and secret checks**

Run:

```powershell
Test-Path 'src/awesome_agent/application/headless.py'
Test-Path 'protocol/fixtures/v1'
rg -n "CommandStatus|status/content/data|z\.record\(.*jsonValueSchema|PresenterRegistry|headless\.LocalApplication" src tui tests protocol docs/architecture ARCHITECTURE.md
git diff --check
git status --short
```

Expected: both `Test-Path` commands print `False`; forbidden legacy searches produce no production/documentation matches; any intentional historical wording in this plan is not treated as product code.

- [ ] **Step 7: Inspect the final diff**

Verify:

- no secrets or private environment values;
- no generated cache or debug output;
- no unrelated Agent, Tool, storage, or visual changes;
- no second command dispatch path;
- no arbitrary command JSON fallback;
- fixture manifest hashes are current;
- deleted v1 and headless files have no live import.

- [ ] **Step 8: Commit documentation and recorded evidence**

```powershell
git add ARCHITECTURE.md docs/architecture docs/superpowers/plans/2026-07-13-pr1-single-command-authority-and-typed-contracts.md
git commit -m "docs: document typed command authority"
```

- [ ] **Step 9: Push, open, and merge PR1**

```powershell
git push -u origin codex/pr1-typed-command-authority
$prUrl = gh pr create --base codex/tui-command-visual-consistency --head codex/pr1-typed-command-authority --title "refactor: establish typed command authority" --body-file .codex/pr-bodies/pr1-typed-command-authority.md
$prNumber = gh pr view codex/pr1-typed-command-authority --json number --jq .number
gh pr checks $prNumber --watch
gh pr merge $prNumber --merge --delete-branch
git switch codex/tui-command-visual-consistency
git pull --ff-only
```

Before these commands, create `.codex/pr-bodies/pr1-typed-command-authority.md` with Summary, Validation, Deferred checks, Risks, and Follow-up sections. `.codex/` remains ignored coordination state and the PR body must not contain secrets. Record `$prUrl` and `$prNumber` in the execution log. Merge only when the diff is scoped, conflict-free, and all required checks pass.

## PR1 Completion Gate

PR1 is complete only when all statements are true:

- `application/headless.py` and Protocol v1 fixtures are deleted.
- `LocalApplication` in `application/facade.py` remains the only surface-facing Application API.
- `composition.py` contains no command semantics or command-result construction.
- the complete dispatcher owns exactly twenty-one non-Ink commands.
- Skill-owned `init` enters the same Core dispatcher and preserves its Agent Turn behavior; four Ink-owned commands never enter Core RPC.
- every Python command success uses one exact semantic payload.
- every command input request uses one exact interaction variant.
- every command failure uses `CommandError` and a stable safe code/message.
- Python-generated v2 fixtures cover every outcome variant and TypeScript parses them exactly.
- TUI command handling has no legacy optional-field inspection or arbitrary JSON fallback.
- selected unavailable credential sources do not silently fall back.
- no new persistence, dependency, service process, or visual redesign was introduced.
- all Task 8 validation evidence is recorded.
- PR1 is merged into `codex/tui-command-visual-consistency` before PR2 is detailed or executed.
