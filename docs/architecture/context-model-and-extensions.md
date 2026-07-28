# Context, model, and extensions

The Agent can reason only over a bounded sequence of provider-neutral messages.
Awesome therefore treats context construction as an explicit, inspectable
pipeline rather than concatenating every available document into a prompt.
Model providers and extensions supply data to that pipeline or tools to the
shared executor; neither may become a new policy authority.

## Context ownership

`src/awesome_agent/context/` owns generic source models, token estimates,
ordering, deduplication, budgeting, explicit-path snapshots, and compression.
`application/context.py` owns product-specific access to the current Thread,
Turn, workspace, Skills, and Memory. Agent nodes invoke that service explicitly.

The pipeline consumes frozen Turn identity, configured/model limits, labeled
sources, and reserved active-tail capacity. It outputs provider-neutral
messages, a provenance manifest, measured budget facts, and a compression
signal. It never outputs permission grants or concrete provider payloads.

```text
Application accepts Turn
  -> capture natural input + explicit path snapshots + local memory
  -> Agent prepare_context
  -> ApplicationContextService builds source list
  -> ContextBuilder orders, deduplicates, budgets, and renders messages
  -> PreparedContext(messages, manifest, limits, compression signal)
  -> Agent checkpoint + model request
```

Context code cannot import Application or provider implementations. Agent code
cannot discover files or query concrete repositories behind the service.

## Source model and order

Each `ContextSource` has a kind, stable source ID, content, provider role,
mandatory flag, optional token budget, and optional covered transcript range.
The current source order is:

1. product instructions;
2. root workspace instructions;
3. the bounded automatic Skill catalog or one selected Skill;
4. user memory;
5. workspace memory;
6. Mem0 recall;
7. Thread summary;
8. recent Turns and direct commands in sequence order;
9. explicit path snapshots;
10. current input;
11. an open assistant/tool chain during compression or recovery.

Ordering is semantic. Instructions must precede conversation; a summary must
not reorder direct commands; the current input must be last among ordinary
base sources. Changing enum order therefore changes prompt behavior and frozen
manifest validation.

The following sources are mandatory once selected: product and model identity,
workspace instructions, the bounded automatic Skill catalog or selected Skill,
explicit paths, current input, and an open tool chain. They are never silently
truncated to make a prompt fit. The Skill catalog is bounded before it becomes
mandatory. If mandatory plus reserved context exceeds the effective input
limit, the Turn fails with a context overflow instead of changing instruction
meaning or dropping a tool observation.

## Budget calculation

Every configured model currently advertises a 262,144-token context limit. The
effective input budget reserves output capacity and applies a compression
threshold in `context/tokens.py`. Exact tokenization remains an estimate, so the
system leaves margin rather than claiming provider-perfect counts.

Long-term memory is optional and receives at most the smaller of 16,384 tokens
or 10% of the effective input limit. That allocation is split:

| Source | Fraction of memory allocation | Hard cap |
| --- | ---: | ---: |
| user memory | 25% | 4,096 tokens |
| workspace memory | 50% | 8,192 tokens |
| Mem0 | 25% | 4,096 tokens |

Optional sources are truncated by their source budgets and remaining capacity.
Mandatory sources are measured whole. Explicit path snapshots share up to 25%
of the effective input budget at Turn capture, with independent file, line,
directory-entry, and path-count bounds.

## Deduplication and provenance

Every retained source produces a `ContextManifestItem` containing kind,
source ID, order, estimated tokens, truncation, SHA-256 content hash, and any
covered transcript sequence range. Skill sources additionally carry a strict
tuple of versioned package identities and descriptive `allowed-tools` values.
That tuple is persisted on the Turn and checkpoint and survives compression.

Content deduplication applies only where semantics permit it:

- mandatory sources are never removed because another layer has identical
  text;
- timeline entries are never collapsed by content;
- local and cloud long-term memory deduplicate normalized content across their
  shared untrusted layer;
- other optional sources deduplicate within kind and role.

This preserves the fact that `AGENTS.md` was a workspace instruction even if it
matches product or Skill text. The manifest is used by `/context`, completion,
and recovery; it is not merely debug output.

## Workspace instructions

After trust, Awesome reads only the root `AGENTS.md` once for the session. The
read is identity-checked and bounded to 32 KiB, then to the smaller of 8,192
tokens or 10% of the effective input budget. Unsafe, binary, non-UTF-8, changed,
or oversized content is ignored as a whole and represented by a structured
diagnostic.

The snapshot is immutable for the session. This avoids rules changing halfway
through a Turn or recovery. The tradeoff is that editing `AGENTS.md` requires a
new Awesome session. Hierarchical instruction files and fallback names are not
part of the current contract.

## Compression

Compression summarizes only bounded base conversation context. The active
assistant/tool tail is extracted, validated, reserved in the target budget,
and appended exactly once after the rebuilt base.

```text
prepared messages near threshold
  -> plan summary range
  -> bounded completion request
  -> persist Thread summary
  -> rebuild mandatory + optional base sources
  -> append unchanged active tool tail
  -> validate messages and manifest
  -> continue model loop
```

Compression uses the same provider retry budget as the Turn. If the mandatory
base plus active tail cannot fit, the Turn terminates with
`context_unrecoverable`; it does not discard or replay an observation.

## Provider-neutral model boundary

`modeling/` defines messages, tool schemas and calls, stream events, errors,
usage, catalog profiles, and `ModelProvider`. A provider implements only:

```text
provider_id
stream(ModelRequest) -> async ModelStreamEvent sequence
```

The frozen, provider-neutral model directory has one shape and one instance:

```text
MODEL_CATALOG
  -> ProviderDescriptor
       -> ModelProfile
```

It is the sole source of supported model identities, capabilities, context
limits, provider-local defaults, supported regions, and credential association.
The current directory contains exactly two Providers and four model profiles:

| Provider | `credential_id` | Regions (default) | Model | Context | Tools | Reasoning | Provider default |
| --- | --- | --- | --- | ---: | --- | --- | --- |
| `deepseek` | `deepseek` | none | `deepseek/deepseek-v4-flash` | 262,144 | yes | yes | yes |
| `deepseek` | `deepseek` | none | `deepseek/deepseek-v4-pro` | 262,144 | yes | yes | no |
| `kimi` | `kimi` | `cn`, `global` (`cn`) | `kimi/kimi-k2.6` | 262,144 | yes | yes | yes |
| `kimi` | `kimi` | `cn`, `global` (`cn`) | `kimi/kimi-k2.5` | 262,144 | yes | yes | no |

The catalog does not say that a credential is present or choose the active
model or region. Credential source and presence, `providers.default_model`, the
Thread selection, and the configured Kimi region remain dynamic
Application/configuration state. A catalog default is only the deterministic
fallback when exactly one model Provider is configured.

Concrete DeepSeek and Kimi adapters live in `providers/` and are instantiated
only by Application composition. They translate SDK payloads into neutral
events and normalize errors; Agent and Context never import the OpenAI client
or a provider adapter.

Provider resource composition also stays in `providers/`. One managed factory
captures the candidate configuration and creates at most one reusable async SDK
client per configured provider. `RuntimeResources` caches the neutral
`ModelGateway` by provider and model, so multiple models share their provider
client without reading mutable Application state. Candidate retirement closes
the owned clients; an injected gateway factory remains borrowed. Credential
validation uses a separate client per attempt and closes it on success, error,
timeout, or cancellation within a bounded cleanup deadline.

`ModelGateway` freezes one catalog selection and enforces stream behavior. It
retries only a retryable failure that occurs before any visible output or
completion, reports retry events, preserves cancellation, and requires exactly
one matching completed model Turn. Once text, reasoning, or a tool call is
visible, transparent replay would duplicate observable work and is forbidden.

The catalog describes supported models; it does not construct clients. The
concrete factory and adapter dispatch remain in `providers/`, with the explicit
official endpoints `https://api.deepseek.com`, `https://api.moonshot.cn/v1`,
and `https://api.moonshot.ai/v1`. This is not a provider registry or DI
container, and no speculative third model Provider exists. Web Provider
selection and catalog concerns remain in the separate Web/configuration
boundary; Tavily Web search/fetch capability never appears in `ModelCatalog`.

Application publishes the catalog through Protocol v5 `ApplicationState`
beside dynamic `provider_credentials`. The TUI validates and derives startup
and credential setup from those fields instead of copying a model or Provider
enum. Application derives `/model` options from the same catalog as a
`CommandSelection`, which the TUI renders generically. At the Python boundary,
dependency direction is `config -> modeling`; `modeling/` no longer imports
configuration. Application combines both with the concrete Provider factory.

The curated catalog is closed rather than accepting arbitrary provider/model
strings. This limits flexibility, but it lets configuration, capabilities,
context limits, identity reporting, and tests agree on the supported product.

## Skills

Skills provide bounded instruction packages. Discovery precedence is bundled,
then user, then workspace; a later source with the same name shadows the
earlier descriptor and emits a diagnostic. Disabled names are excluded. Every
effective descriptor receives a versioned identity derived from normalized
metadata and the pinned `SKILL.md` fingerprint and content.

Workspace Skills are more strictly handled because their path is controlled by
the trusted project:

```text
workspace anchor
  -> .awesome
  -> skills
  -> package directory
  -> SKILL.md / resource
```

Every component must be a plain directory or file, never a symlink, junction,
or other reparse point. Discovery stores anchor, root, package identities, and
the initial `SKILL.md` fingerprint. Load and resource reads reopen the pinned
tree, verify those identities and containment, then perform a bounded UTF-8
read. Bundled and User packages pin their package and `SKILL.md` identities;
Workspace packages additionally pin the complete trusted-anchor chain.
Replacing a package after discovery therefore fails closed. One invalid package
produces a diagnostic without suppressing valid packages.

The discovery fingerprint applies to `SKILL.md`, not to every resource. A
resource traversal proves that its components are plain, contained, and stable
across that individual checked open, but it does not compare ordinary nested
directories or resource content with a discovery-time identity. A safe
replacement completed before the resource read can therefore be observed.

Local User package management is an Application use case, not an Agent tool or
a second discovery implementation:

```text
awesome skills CLI
  -> argument parsing + optional TTY confirmation
  -> private Protocol v5 skill.list / skill.install / skill.remove
  -> Application SkillManagementService
  -> one blocking worker operation
  -> SkillPackageManager validation + recoverable package transaction
  -> <AWESOME_HOME>/skills
```

The package RPCs are admitted only while Application is exactly
`UNINITIALIZED`; one Application-owned pre-initialize guard makes them mutually
exclusive with each other and with `initialize`. The RPC itself never constructs
a `WorkspaceRuntime`, Thread, Turn, graph, model, or Tool Executor and leaves the
phase unchanged. The Node launcher owns only command syntax, stable output, and
removal confirmation; Core alone owns manifest, archive, path, size, identity,
locking, and recovery rules. The official CLI receives one bounded product
result and closes the private Core. A different private client may mutate the
same still-uninitialized Core and then initialize it; discovery sees the changed
package. Once a Session is initialized, its catalog remains immutable and is
never hot-updated.

Fresh install publishes a fully validated stage to an absent target with one
same-directory no-replace rename. Replace is a recoverable sequence of two
forward renames—target to quarantine, then stage to target—not one atomic
replacement. Remove likewise quarantines the target before published cleanup.
The marker drives rollback before publication and roll-forward cleanup after
publication. Caller cancellation waits for the owned worker to converge without
a wall-clock cleanup deadline, then re-raises cancellation.

`auto` freezes a deterministic catalog of at most 64 identities and exposes
`load_skill` plus `read_skill_resource`; it does not execute a Skill. `off`
freezes no Skill source and exposes neither tool. A named mode eagerly freezes
the body and identity as mandatory system context and exposes only
`read_skill_resource` for that package.

Both tools use `context.read`. Registration-owned hard admission matches the
operation and package identity against the frozen Turn scope before permission
policy, and the handler checks the identity again before returning content.
Recovery therefore keeps the checkpoint's authority even if a rebuilt Runtime
discovers a different package. `allowed-tools` describes intended compatibility
but never grants permission or bypasses the shared Tool Executor.

## Local and cloud Memory

Local user and workspace Memory are independent, disabled by default, and read
as untrusted reference Markdown. Managed entries have stable identities;
duplicate normalized facts across user and workspace sources are removed from
the lower-priority rendered copy.

Mem0 Cloud is an optional adapter and the only external memory provider today.
Recall is query-bounded, identity-scoped, deduplicated against local memory,
and represented as untrusted context. Cloud failure becomes a diagnostic; it
does not make the whole Turn configuration invalid. Post-answer distillation
uses a separate policy and never uploads raw transcript by implication.

`memory/finalization.py` owns `Mem0PostAnswerFinalizer`, the Memory-side
implementation of Agent's generic `PostAnswerFinalizer` port. Application wires
it only for an enabled, complete Mem0 session; otherwise it injects Agent's
disabled implementation. The adapter translates Mem0 identities, distillation
statuses, and `Mem0Diagnostic` values inside the Memory boundary. It returns
the original answer, the distiller's model-call/usage accounting, and generic
`PostAnswerDiagnostic` values whose codes are retained and whose message is the
fixed `Optional memory operation did not complete.`. After constructing that
result, it attempts to project the enabled Memory status. A failed status
projection is retained as `memory_status_projection_failed` with the fixed
message `Optional memory status projection failed.` and does not discard the
answer or accounting. Status-projection cancellation is not converted into a
diagnostic: the original cancellation propagates to the Agent boundary. Agent
sees none of the Mem0-specific types.

The generic request can carry ordered tool citations, but the current Mem0
implementation does not consume them, rewrite citation markers, or alter the
answer. Invalid output, budget overrun, and unexpected failure become Agent
warnings while preserving the already-generated answer. Cancellation that
escapes the adapter preserves the prior checkpointed answer and is immediately
re-raised by Agent without another warning projection; see
[Application and Agent](application-and-agent.md#post-answer-finalizer-port).

The Mem0 SDK currently performs synchronous credential validation in its async
client constructor. Awesome runs that constructor in a cancellation-aware worker
instead of blocking the event loop, then registers only an internally created
client with the runtime exit stack. An injected client is borrowed. If the SDK
constructor outlives the bounded cancellation cleanup, Python cannot stop that
worker, so Awesome returns cancellation without waiting indefinitely and closes
the eventual client through a late-completion cleanup hook.

Memory tools have their own memory policy. Enabling Memory does not grant
workspace, shell, or MCP capabilities.

## MCP catalog and calls

MCP extends the shared tool registry, not the Agent graph. One server-specific
lock covers catalog loading, compilation, and publication:

```text
start stdio client
  -> initialize
  -> consume all tool pages within bounds
  -> compile every input/output JSON Schema without network retrieval
  -> assign generation
  -> validate the final namespaced names
  -> build all generation-bound handlers
  -> Registry validates aggregate bounds and atomically replaces namespace
  -> without awaiting, Manager publishes generation + client + catalog + CONNECTED
```

The catalog compiler defaults to JSON Schema Draft 2020-12 and accepts only a
supported explicit dialect and required vocabularies. It validates standard
composition, conditionals, ranges, patterns, arrays, and property constraints.
`format` keeps JSON Schema's default annotation semantics. `$ref`,
`$dynamicRef`, and `$recursiveRef` must resolve within the same schema resource;
remote references are rejected before a validator can perform I/O.

Per-server catalog bounds are 128 tools, 128 pages, 256 KiB per input or output
schema, 1 MiB for the full catalog, schema depth 64, and 128 characters for the
final `mcp.<server>.<tool>` name. The shared Registry separately bounds the
effective aggregate across built-ins and every extension namespace to 128 tools
and 1 MiB of canonical model-facing definitions (`name`, `description`, and
`input_schema`). A per-server-valid candidate can therefore still fail the
shared budget.

Registry replacement first validates the complete aggregate candidate and then
swaps the namespace all-or-none. Once that synchronous call succeeds, no
`await` separates the Manager assignments for the same generation and the
final `CONNECTED` state. `CONNECTED` therefore means the live client, compiled
catalog, and complete Registry namespace have all been published. A duplicate
name, invalid contract, cursor cycle, timeout, or either kind of bound violation
closes the new client, invalidates the generation, removes that server's
namespace, and reports one fixed, bounded diagnostic. It never publishes a
valid subset or removes another server's committed namespace.

Handlers capture the catalog generation. Before remote I/O, Pydantic validates
arguments using the compiled schema; the manager then verifies that server,
tool, and generation are still current. Restart removes the old namespace
before reconnecting. `call_tool()` never lazily reconnects.

The inner MCP call deadline is 30 seconds and the Tool Executor envelope is 40
seconds. Timeout or connection loss invalidates the catalog and returns an
`UNCERTAIN_OUTCOME`: the external server may have executed the action. Awesome
does not reconnect or replay it in the same Turn. Cancellation performs bounded
connection cleanup and continues propagating cancellation.

Structured output is bounded before schema traversal and rendering. A declared
`outputSchema` must match `structuredContent`; otherwise the call fails without
exposing arguments or schema details. Text, media-block count, JSON bytes,
node count, and depth all have independent limits.

## Extension invariants

Every extension must preserve these rules:

- context is labeled, bounded, and has manifest provenance;
- extension text is not permission or policy;
- tools enter the existing registry namespace and shared executor;
- validation happens before approval and external I/O;
- cancellation is propagated, not converted to a normal error;
- uncertain external effects are not automatically replayed;
- one bad package or server does not corrupt unrelated sources;
- a new provider abstraction is justified by a real second implementation.

## Design tradeoffs

- Mandatory source preservation can fail a Turn that a lossy prompt builder
  might squeeze through; it avoids silently changing rules or tool history.
- Estimated tokens and reserved margin sacrifice some capacity for
  provider-neutral predictability.
- A closed model catalog requires code changes for a new model but keeps
  capabilities, limits, credentials, and UI selection coherent.
- Complete-snapshot MCP publication delays tool availability until both the
  Manager catalog and Registry aggregate are valid; this prevents partial
  catalogs, partial namespaces, stale handlers, and overlong downstream names.
- Immutable workspace instructions and the pinned Skill package/`SKILL.md`
  lineage require a new session after edits; lazily read Skill resources remain
  safe per-open reads rather than a whole-package content snapshot.

## Source and test map

- Context: `context/builder.py`, `context/models.py`, `context/tokens.py`,
  `application/context.py`
- Instructions and paths: `context/workspace_instructions.py`,
  `context/path_refs.py`
- Compression: `context/compression.py`, `agent/nodes.py`
- Models: `modeling/`, `providers/deepseek.py`, `providers/kimi.py`
- Skills: `extensions/skills/discovery.py`, `loader.py`
- MCP: `extensions/mcp/catalog.py`, `manager.py`, `adapter.py`, `stdio.py`
- Memory: `memory/finalization.py`, `memory/`
- Tests: `tests/unit/context/`, `tests/integration/test_context_pipeline.py`,
  `tests/integration/test_skills_mcp.py`,
  `tests/structural/test_context_architecture.py`,
  `tests/structural/test_extension_architecture.py`
