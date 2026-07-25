# Extending Awesome

An extension is successful when it enters an existing authority boundary. A
provider translates model I/O, a tool enters the Registry/Policy/Executor path,
a Skill contributes bounded instructions, and an MCP server contributes an
all-or-none compiled Manager catalog followed by an all-or-none Registry
namespace replacement. Those two commits are not one transaction. None creates
a second Agent loop, command runtime, permission system, or database owner.

## Decide whether to extend

Before adding an abstraction, answer:

1. What user workflow cannot be expressed by an existing provider, tool,
   command, Skill, or MCP server?
2. Which package already owns the decision?
3. Is this a second concrete implementation or only a predicted future need?
4. What inputs are untrusted, and where are they validated and bounded?
5. What happens on timeout, cancellation, partial output, and process crash?
6. Is an external effect safe to retry, or must it become uncertain?
7. Does this change Protocol v3, storage schema, permissions, or packaging?

Prefer a concrete implementation behind a current contract. Do not create a
generic provider/backend/plugin layer for one hypothetical implementation.

## Add a model provider

Providers implement the neutral `ModelProvider` protocol in
`modeling/provider.py`:

```python
class ModelProvider(Protocol):
    @property
    def provider_id(self) -> ProviderId: ...

    def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]: ...
```

The adapter receives neutral messages/tool definitions and yields only neutral
stream events. It must not import Application, Agent, Storage, Context, or TUI.

### Required design work

A real provider addition usually changes all of these closed contracts:

- `ProviderId` and continuation/error identity in `modeling/turns.py`;
- curated model profiles, capabilities, defaults, and context limits in
  `modeling/catalog.py`;
- strict configuration, credential status, environment source, and help URL;
- one concrete adapter under `providers/`;
- provider factory and Application composition;
- `/model`, `/auth`, `/config`, `/status`, and `/doctor` facts;
- Protocol fixtures and strict TUI schemas/pickers when public enums change;
- docs, tests, dependency inventory, installer/release verification if a new
  runtime dependency is required.

Do not accept arbitrary model strings merely to avoid updating the catalog.
The closed catalog is how supported capabilities and context limits stay
consistent across configuration, Agent, and UI.

### Stream invariants

An adapter must:

- preserve request message/tool ordering;
- assemble fragmented tool arguments deterministically;
- produce stable call IDs and indices;
- normalize stop reasons and usage;
- classify authentication, rate limit, timeout, connection, transient,
  invalid-request, context-length, and protocol failures;
- mark retryability exactly as the neutral error contract requires;
- propagate `CancelledError` without translating it;
- emit at most one `TurnCompleted` or one terminal `TurnFailed`;
- never log or include secret values or raw unbounded provider payloads.

`ModelGateway` retries only a retryable failure before visible output or
completion. The adapter must not add a competing transparent retry after text,
reasoning, or tool calls become visible.

### Provider tests

At minimum cover:

- text-only, reasoning, tool call, mixed delta, and completion streams;
- fragmented/invalid tool arguments and duplicate/unknown fields;
- each normalized error class and retryability;
- cancellation during iteration;
- provider/model identity mismatch;
- usage and continuation handling;
- no retry after visible output;
- selection with missing/wrong credentials;
- a Gateway integration test using a fake client.

Live credentials belong only in explicitly enabled external release tests.

## Add a built-in tool

A built-in is appropriate when Awesome itself must guarantee the behavior,
policy, Change Journal integration, and cross-platform lifecycle. A project-
specific integration is usually better as MCP.

### 1. Define strict arguments

Use a Pydantic model with length, range, and shape bounds. Reject extra fields
unless the existing contract explicitly permits them. Expensive parsing or I/O
must happen after validation.

```python
class InspectArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=1_000)
    max_items: int = Field(default=100, ge=1, le=1_000)
```

### 2. Choose one capability

Use the narrowest current capability:

- `workspace.read`;
- `workspace.write`;
- `workspace.delete`;
- `shell.execute`.

If none fits, adding a capability is a permission-model change. Define behavior
for Request approval, Accept edits, Full access, temporary grants, MCP/unknown
handling, and hard denial before implementing it.

### 3. Implement one handler

The handler accepts validated arguments plus `ToolExecutionContext` and returns
`ToolOutput`. Expected user/environment failures raise `ExpectedToolFailure`
with a stable `ToolErrorCode`. Unexpected invariant violations should escape so
the executor terminates the Turn visibly.

Read tools use the shared workspace policy and bounded identity-checked readers.
Write/delete tools must mutate through the Change Journal and pinned filesystem
primitives. Do not call `Path.resolve()`, `read_text()`, `write_text()`,
`unlink()`, or a subprocess directly as an alternate effect path.

### 4. Register once

Register the `ToolSpec`, input model, handler, capability, read-only flag,
display verb, and optional internal timeout resolver in the built-in registry
composition. The name must match the shared tool-name pattern and cannot collide
with built-in or extension namespaces.

The model-visible schema comes from the input model. Internal cleanup budgets
must not appear as fake model arguments.

### 5. Define lifecycle behavior

Document and test:

- argument and path validation before approval;
- hard-deny rules before mode grants;
- approval text derived from validated operation facts;
- total timeout and handler cancellation grace;
- output/content/presentation bounds;
- one ToolActivity and one terminal event;
- whether an attempt is recorded before an irreversible effect;
- whether the effect is fully, partially, or not reversible;
- crash and startup-recovery evidence.

### Tool tests

Add unit tests for schema, normal result, expected error, unknown capability,
all permission modes, hard denial, timeout, cancellation, and output bounds.
Add integration tests when the tool touches workspace identity, Change Journal,
processes, Application approval, transcript activity, or recovery. Test real
platform primitives on their owning OS.

## Extend shell behavior

Do not add a second shell parser or evaluate a command only in the UI. Extend
the pure dialect-aware policy in `core/tools/command_policy.py` and run its
explicit CMD, POSIX, and PowerShell test matrix on any host.

A policy rule needs:

- normalized executable spellings and absolute paths;
- suffix/case variants;
- compound, pipeline, newline, and known-wrapper nesting;
- working-directory changes;
- encoded or literal wrapper payloads where supported;
- a benign command containing the same words;
- all three permission modes and direct execution;
- proof that a denial never starts the Process Runner.

The circuit breaker should be described as known-accident prevention, not
general malicious-code detection.

## Add a slash command

Commands are deterministic Application or Ink operations. They never submit a
hidden Agent prompt.

1. Add the name and owner to `CommandName`/`COMMAND_OWNERS`.
2. For Core ownership, add one focused command service handler and wire it into
   the complete immutable dispatcher in composition.
3. Decide whether it is a side-effect-free observation or needs an exclusive
   foreground lease. Observation status is a narrow concurrency contract, not
   a convenience flag.
4. Define a discriminated `CommandOutcome` payload and optional authoritative
   state effect.
5. Update Protocol fixtures, TypeScript schema, catalog, parser/help, exhaustive
   Presenter, and UI-flow tests.
6. Document input, empty state, interaction, error, and busy behavior.

Do not format terminal output in Python or add a generic object renderer in
TypeScript.

## Author a Skill

A Skill is a package directory whose name matches its `SKILL.md` frontmatter
`name`. Frontmatter can declare description, allowed tools, license,
compatibility, and metadata. Body and resources are UTF-8 text with bounded
reads.

Skill instructions are context. `allowed-tools` states compatibility; it does
not authorize tools. A Skill needing project-specific actions should call an
existing tool or use an MCP server rather than embedding another executor.

For bundled Skills:

1. add the package under `src/awesome_agent/extensions/skills/bundled/`;
2. use the normative bounded string/list frontmatter types even though the
   current parser still coerces some scalar values;
3. explain when the Skill should be selected and how it stops;
4. keep referenced resources inside the package;
5. add discovery, load, resource, token-bound, and packaging tests.

Workspace Skill code must retain anchor/package identity revalidation. Do not
weaken it to support a linked package; use a user Skill if the user intentionally
manages a linked layout.

## Integrate through MCP

MCP is the preferred boundary for independently operated tools. Server
configuration is secret-free: command, arguments, environment variable names,
source, and enabled state. Secret values are resolved from the environment,
never workspace YAML.

The client must complete and compile the entire paginated catalog before
publishing any registry item. Standard JSON Schema constraints are supported,
but references must remain local and network retrieval is forbidden. Respect
tool/page/schema/catalog/depth limits.

Do not:

- lazily reconnect in `call_tool()`;
- preserve an old registry namespace during restart;
- register a valid subset of an invalid catalog;
- validate arguments after approval or remote I/O;
- reuse an old validator with a new catalog generation;
- replay a timed-out or disconnected call in the same Turn;
- force JSON Schema `format` checks while claiming default semantics.

MCP input validation errors must be generic and must not expose raw arguments
or schemas. Output validation and JSON traversal occur under their own byte,
node, depth, and content-block bounds.

## Add a Memory provider

Awesome currently has local Markdown Memory and one optional Mem0 Cloud
adapter. A second external provider can justify a neutral abstraction; do not
generalize the current Mem0 client before that implementation exists.

A provider design must define:

- stable user/workspace identity;
- independent enablement and credential availability;
- bounded recall and deduplication against higher-priority local memory;
- untrusted-context labeling;
- timeout, cancellation, redacted diagnostics, and offline behavior;
- whether post-answer writes are distilled facts or raw transcript;
- deletion/conflict semantics and user controls.

Memory cannot grant Tool capabilities or become a hidden provider fallback.

## Change storage or recovery

Storage changes require more than adding a column. Determine whether absence
has a safe interpretation under Schema 7. If not, increment schema identity and
define product behavior for older/newer state. Awesome has no automatic
migration framework, so a new schema must update preflight, reset verification,
packaging checks, docs, and recovery tests.

For every new durable fact specify:

- owning package and table/path;
- transaction that creates or changes it;
- relation to graph checkpoints and ChangeSets;
- crash windows and compare-and-swap conditions;
- terminal/cancellation cleanup;
- reset inclusion or preservation;
- bounded serialization and forward/legacy interpretation.

Never make Application reconstruct LangGraph channels or let a checkpoint
become the product transcript.

## Add a protocol or TUI surface fact

Follow the full Protocol v3 chain:

```text
Python strict model
  -> facade/method/event owner
  -> fixture generator
  -> valid + invalid v3 fixtures
  -> TypeScript strict schema
  -> Surface effect/reducer if authoritative state changes
  -> exhaustive Presenter/component
  -> contract + UI-flow tests
```

Unknown fields are errors. Optional and nullable are distinct. Preserve safe
integer limits and frame bounds. A future non-Ink surface adapts the facade and
events; it must not call Agent, tools, or storage directly.

## Extension review checklist

- [ ] One existing package owns the new behavior.
- [ ] No second graph, executor, command runtime, policy, or storage owner.
- [ ] Input is strict and bounded before approval/external I/O.
- [ ] Capability and hard-deny behavior are explicit.
- [ ] Timeout, cancellation, cleanup, and uncertain outcome are tested.
- [ ] Durable facts and recovery rules are documented.
- [ ] Secrets and raw payloads cannot enter events, audit, fixtures, or logs.
- [ ] Dependency ownership and packaging were reviewed.
- [ ] Protocol/TUI contracts were updated together when crossed.
- [ ] User and architecture docs explain what, how, why, limits, and tradeoffs.
- [ ] Focused, integration, structural, platform, and release evidence match the
      actual risk.
