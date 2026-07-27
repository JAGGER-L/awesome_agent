# Awesome Architecture

Awesome is a terminal AI coding assistant. One `awesome` launcher starts a
private Python process and selects the Ink interface, one headless Turn, or one
local User Skill package command; runtime and package rules remain in the
Python Core, while TypeScript parses public intent and projects typed results.

This document is the authoritative technical overview. Focused documents under
[`docs/architecture/`](docs/architecture/README.md) explain individual
boundaries without redefining the system.

## System Overview

```text
┌───────────────────────────────────────────────────────────────────────────┐
│                         Entry & Presentation                              │
│                                                                           │
│  awesome launcher                  Ink TUI / Headless run / Skills CLI   │
│  CLI arguments                     Input / UX / Result projection        │
└───────────────────────────────────┬───────────────────────────────────────┘
                                    │
                                    │ JSON-RPC 2.0 / NDJSON over stdio
                                    ▼
┌───────────────────────────────────────────────────────────────────────────┐
│                      Python Application Host                              │
│                                                                           │
│  ApplicationFacade   Commands       Interactions     Event Projection     │
│  Workspace Trust     Thread/Turn    Cancellation     Composition          │
└──────────────┬──────────────────┬──────────────────┬──────────────────────┘
               │                  │                  │
               ▼                  ▼                  ▼
┌────────────────────────┐ ┌───────────────────┐ ┌──────────────────────────┐
│ Agent Core             │ │ Extensions        │ │ Local State              │
│ LangGraph              │ │ Skills / MCP      │ │ Application SQLite       │
│ Context Assembly       │ │ Local Memory      │ │ LangGraph Checkpoints    │
│ Model / Tool Loop      │ │ Mem0 Cloud        │ │ Change Journal           │
│ Compression / Budgets  │ │                   │ │ TUI Preferences          │
└───────────┬────────────┘ └─────────┬─────────┘ └──────────────────────────┘
            │                        │
            ├──────────────┬─────────┘
            ▼              ▼
┌────────────────────┐  ┌──────────────────────────────────────────────────┐
│ Model Providers    │  │ Tool System                                      │
│ DeepSeek / Kimi    │  │ Registry -> Policy -> Executor -> Result/Event   │
└────────────────────┘  └────────────────────────┬─────────────────────────┘
                                                 │
                                                 ▼
                                   ┌────────────────────────────┐
                                   │ Workspace & Host           │
                                   │ Files / Shell / Git        │
                                   │ Tests / Build Tools        │
                                   └────────────────────────────┘
```

The Application Host is the composition and lifecycle boundary. It invokes the
compiled LangGraph directly; it does not implement a second graph engine or
copy graph channel state. Model calls use provider-neutral contracts, and every
tool call follows the same Registry, Policy, and Executor path.

## Directory Structure

```text
awesome_agent/
├── src/awesome_agent/
│   ├── agent/          # LangGraph state, nodes, routes, budgets
│   ├── application/    # lifecycle, commands, operations, composition
│   ├── config/         # user/workspace configuration and precedence
│   ├── context/        # context assembly, token estimates, compression
│   ├── conversation/   # Thread, Turn, transcript, repository contracts
│   ├── core/
│   │   ├── changes/    # Change Journal and undo/redo contracts
│   │   ├── tools/      # tool registry, policy, executor, built-ins
│   │   ├── workspace/  # workspace identity and trust models
│   │   ├── filesystem.py       # identity-bound filesystem primitives
│   │   └── process_lifetime.py # Core process-tree ownership
│   ├── extensions/
│   │   ├── mcp/        # MCP stdio client and tool adapter
│   │   └── skills/     # Skill discovery, loading, package management, tools
│   ├── memory/         # USER.md, MEMORY.md, Mem0 Cloud, memory tools
│   ├── modeling/       # provider-neutral messages and model gateway
│   ├── protocol/       # JSON-RPC types and private stdio Host
│   ├── providers/      # DeepSeek and Kimi adapters
│   ├── safety/         # redaction helpers
│   ├── storage/        # embedded SQLite and checkpoint adapters
│   ├── web/            # Tavily HTTP adapter for Core's provider-neutral Web port
│   ├── paths.py        # AWESOME_HOME path ownership
│   └── version.py      # product version reader
├── tui/                # Ink + React presentation package
├── protocol/fixtures/  # cross-language protocol fixtures
├── scripts/release/    # release bundle builder
├── tests/              # unit, integration, E2E, packaging, structural
├── install.sh
├── install.ps1
├── pyproject.toml
└── VERSION
```

Generated environments, caches, development plans, and user secrets are not
part of the product source tree.

## Recommended Reading Order

Start with the level of detail needed for the question:

1. [How Awesome works](docs/concepts/README.md) for the product mental model.
2. [Request lifecycles](docs/architecture/request-lifecycles.md) for startup,
   Turn, direct-command, approval, cancellation, and recovery sequences.
3. [Architecture reading path](docs/architecture/README.md) to choose a focused
   subsystem guide.
4. This document for the complete topology, dependency direction, and state
   ownership contract.

For source study, continue in dependency order:

1. `src/awesome_agent/application/facade.py` — surface-facing product API.
2. `src/awesome_agent/application/composition.py` — concrete dependency wiring.
3. `src/awesome_agent/application/turns.py` — Turn lifecycle and recovery.
4. `src/awesome_agent/agent/graph.py` — the only graph compiler.
5. `src/awesome_agent/agent/nodes.py` — model/tool loop and finalization.
6. `src/awesome_agent/context/builder.py` — prompt context assembly.
7. `src/awesome_agent/modeling/` and `src/awesome_agent/providers/` — model
   contracts and supported adapters.
8. `src/awesome_agent/core/tools/` — Registry, Policy, Executor, and built-ins.
9. `src/awesome_agent/conversation/` and `src/awesome_agent/storage/` — product
   records and embedded adapters.
10. `src/awesome_agent/protocol/stdio.py` — private process boundary.
11. `tui/src/app/App.tsx` — presentation composition.

## Data Flow

### Startup and workspace trust

```text
awesome <current directory>
        │
        ▼
Ink starts awesome-core
        │ initialize(workspace, protocol version)
        ▼
Application resolves canonical workspace
        │
        ├── state current/new ───► continue to workspace trust
        │
        ├── state older ─────────► explicit reset-or-exit interaction
        │                          confirmed reset -> exclusive lease
        │                          atomic state replacement -> trust
        │
        ├── state newer ─────────► stop and ask user to upgrade
        │
        ├── trusted ─────────────► load user/workspace configuration
        │                          snapshot bounded root AGENTS.md
        │                          load Skills and MCP declarations
        │                          create or resume a Thread
        │
        └── not trusted ─────────► interaction.required
                                   Yes -> persist trust -> continue
                                   No  -> exit without persisting denial
```

Project-controlled configuration, instructions, Skills, and MCP declarations
are not loaded before trust is accepted.

Activation also acquires two exclusive session leases: one for the canonical
workspace path key and one for the opened root directory's physical identity.
The path lease survives replacement of that pathname, while the entity lease
collapses alternate spellings of the same directory. This prevents a second
Core from treating a live Turn as crash recovery through either a replacement
root or a path alias.

The root `AGENTS.md` is an immutable session source loaded only after trust. A
bounded identity-checked read either supplies the whole mandatory instruction
source or supplies no content plus a structured diagnostic; it is never
meaning-changing truncation. That diagnostic does not invalidate configuration.

Application state preflight is read-only and runs before trust, checkpoints,
or writable storage. The current format is Schema 8. Product and schema
versions are independent: schema identity changes only with persisted
semantics and increases monotonically. The migration catalog has floor 7,
current 8, and one production `7 -> 8` step that adds nullable Thread lineage.
Schemas 1–6 therefore offer only the typed reset-or-exit interaction; newer,
unknown, corrupt, unreadable, or locked state is never silently deleted.

A registered migration runs only after shared-lease preflight, exclusive
lease acquisition, and a second compatibility check. Storage validates and
atomically publishes a WAL-aware SQLite backup, applies the complete adjacent
chain in one transaction, downgrades the lease, and only then initializes the
Application repositories. Failure rolls the transaction back and retains the
backup for manual recovery; startup never automatically resets or restores it.

### Conversation Turn

```text
User Message
    │
    ▼
turn.submit -> ApplicationFacade.submit_turn
    │
    ├── atomically acquire the foreground Operation lease
    ├── resolve Thread configuration
    └── create Turn and user transcript entry
             │
             ▼
       LangGraph Agent
             │
       prepare context
             │
       call ModelGateway
             │
       ┌─────┴─────┐
       │ Tool Call │── No ──► finalize answer
       └─────┬─────┘
             │ Yes
             ▼
       Tool Executor
             │
       observation + checkpoint
             │
             └──────────────► call model
```

When the graph returns a final answer, Application completes the Turn, appends
the assistant transcript entry, and records bounded usage. It then attempts to
seal the ChangeSet and delete the finished checkpoint before emitting completion
events. Cleanup failure does not overwrite the already-persisted primary Turn
terminal state; startup reconciliation retries stale terminal evidence.

### Tool call

```text
Model ToolCall
    -> bind ToolRegistry snapshot + resolve registration
    -> strict registered-input validation
    -> registered hard admission
    -> typed description exactly once
    -> capability policy + bound approval
    -> registered deadline + handler under cancellation envelope
    -> normalized ToolResult + event + bounded audit/Agent observation
```

The registration owns typed description, hard admission, replay safety, and
deadline resolution. Description runs only after hard admission, so rejected
path-like input cannot trigger description-time probing. The Executor applies
that one sequence uniformly and does not branch on concrete tool names;
capability grants cannot override hard admission.

Core's provider-neutral `Citation(id, title, url)` value follows successful
tool data from `ToolOutput.citations` into `ToolResult.citations`. Text bounding
does not remove that tuple. Agent serializes the complete result into
`AgentState.tool_results` and derives the ordered `AgentState.citations`
snapshot, so both survive checkpoint recovery. Conversation records, Protocol
v4, TUI, and headless output carry the same sources without a compatibility
adapter.

Optional Web access consists of two ordinary registered tools, not a second
execution framework. They appear only when user configuration has
`web.enabled: true` and `TAVILY_API_KEY` resolves. The provider-neutral
`web_search` handler sends a bounded basic search with at most ten results to
`POST https://api.tavily.com/search`. `web_fetch` accepts one public HTTPS URL
and requests basic Markdown extraction from `POST
https://api.tavily.com/extract`, bounded to 24,000 model-visible characters.
Tavily's cloud service connects to the target; Awesome Core never opens a
connection to that URL. The shared explicit `httpx.AsyncClient` uses
`trust_env=False`, no redirects or opaque retries, and only
`AWESOME_WEB_PROXY_URL` when configured. Stable redacted failures cover
credentials, rate/usage limits, timeout, connection, provider availability,
and malformed responses. Both registrations are `non_replayable`.

`network.read` asks on first use in every permission mode and offers deny,
allow once, or allow for the current Thread. Thread grants are cleared by
Thread selection, runtime rebuild, permission-mode change, `/web revoke`,
`/web off`, and shutdown. A Turn owns a frozen `web_requests` budget of at most
eight; approval occurs before the quota is consumed and before HTTP begins.
Search queries and Fetch URLs leave the machine for Tavily under its published
privacy policy and platform terms, while structured diagnostics omit query,
URL, response, and secret text.

File-changing built-ins write through the Change Journal and shared
identity-bound filesystem primitives. Lexical containment is only admission:
the actual mutation pins the workspace and parent directory chain, verifies
the opened object's identity, and refuses link/reparse or hard-link aliases
that cannot be proven to name one workspace object. This narrows
path-replacement races but is not a filesystem compare-and-swap: a
same-privilege host process can replace an in-workspace target after the final
identity check and before replace/remove, and on POSIX can move an already-open
directory after its reachability check. Pinned parents and no-follow operations
prevent those races from following a link to an external target, but the
stronger concurrent-host threat model requires an OS sandbox or mount boundary.
`execute` runs on the host and is not a sandbox. For Agent tool calls, Request
approval asks before writes, deletes, shell, MCP, and unknown capabilities.
Accept edits allows ordinary workspace writes only. Confirmed Full access
allows known built-in local writes, deletes, and shell for its bound Thread;
MCP and unknown capabilities still ask.

Direct `! command` is a deliberate exception to that prompt matrix: the user's
exact input is the authorization, so Application gives that one Direct
Operation an independent Full-access permission session and does not open the
ordinary shell approval interaction. It still follows the same schema,
lexical/pre-spawn circuit-breaker checks, Process Runner, journal, redaction,
timeout, cancellation, and terminal-event path. The circuit breaker is designed
to stop recognizable accidents, not arbitrary hostile obfuscation. Shell
effects may escape the workspace and cannot be reversed by the journal.

### Slash command

```text
/command
   ├── Ink-owned presentation command -> local UI state
   └── Application command -> command.execute -> typed result
```

The authoritative Core command path is:

```text
Ink command controller
    -> Protocol v4 command.execute
    -> LocalApplication facade
    -> complete CommandDispatcher
    -> one focused command service
    -> CommandOutcome
    -> exhaustive TUI Presenter
    -> current transcript path
```

The immutable dispatcher owns every Core command. Ink-owned commands remain
local presentation actions and never enter Core RPC. Composition only wires
command services; it contains no command semantics and constructs no command
results. Slash commands are deterministic product operations and never submit
hidden model prompts; natural-language input is the only path that starts an
Agent Turn.

`LocalApplication` is the only surface-facing Application host. Python produces
Protocol v4 discriminated outcomes that TypeScript validates and presents
exhaustively.
Command progress is pending Surface lifecycle state, not a second durable
operation model.

Conversation discovery has two entry points over the same Application-owned
boundary. Protocol `thread.search` keyset-pages workspace-isolated substring
matches. `/search <query>` presents at most the 50 most recently updated
matches, asks the user to refine the query when more exist, and resumes a
selected Thread through the existing transition payload. Both paths share the
same 5,000,000 SQLite VM-op scan budget and stable `result_too_large` failure.
`/export` is also a deterministic Application command: it projects at most
5 MiB of public transcript data to a normalized 1–1,000-character
workspace-relative Markdown or JSON path, renders away from the event loop,
then reports the safe write through the Change Journal.

### Resume and recovery

`--continue` selects the most recent workspace Thread; `--resume <id>` selects
an exact or unambiguous-prefix Thread, while bare `--resume` opens the recent
Thread picker. On startup, Application reconciles unfinished product Turns with
LangGraph checkpoints:

- a completed graph state is finalized into product records;
- a valid unfinished checkpoint is resumable;
- a missing or corrupt checkpoint fails the Turn with a stable error code;
- a non-replayable tool, or missing/unknown replay metadata, requires an
  explicit retry/abort interaction whose safe default is Abort;
- checkpoints left behind by terminal product Turns are removed.

Recovery resolves the same tool name from the current Runtime Registry and
uses that registration's replay metadata. It never infers replay safety from a
concrete tool name; unknown metadata fails closed and is never retried
automatically.

The latest strictly validated checkpoint is the recovery fact source for an
unfinished Turn. Its frozen manifest may repair the Application SQLite
projection only when that projection is empty or its immutable source anchors
share lineage, and the old projection still matches an explicit
compare-and-swap expectation. This
converges the unavoidable commit window between the separate Application and
LangGraph databases without treating a self-consistent but unrelated snapshot
as authority.

## Major Subsystems

### Application Host

- **Responsibility:** workspace initialization, configuration resolution,
  Thread/Turn lifecycle, commands, foreground operation serialization,
  interactions, cancellation, event projection, recovery, composition, and
  pre-initialize local User Skill package use cases.
- **Does not own:** model reasoning, graph routing, tool implementation, or UI
  rendering.
- **Primary files:** `application/facade.py`, `application/composition.py`,
  `application/bootstrap.py`, `application/turns.py`,
  `application/operations.py`, `application/skill_management.py`.
- **Dependencies:** Agent Core, current adapters, Conversation, Storage, Core,
  Context, Extensions, and Memory.

`LocalApplication` holds the only `ApplicationBootstrap`, and Application is
the sole owner of `BootstrapPhase`. Typed initialize and interaction outcomes
advance or restore that phase; a serialized protocol response is never a
lifecycle fact source. The stdio Host only asks Application whether an
operation is admitted and translates a rejection into the existing Protocol
v4 handshake error. It neither keeps a parallel phase machine nor parses
response payloads to infer readiness. Protocol v4 request, result, and error
shapes are unchanged by this internal ownership move.

The private `skill.list`, `skill.install`, and `skill.remove` operations are
admitted only while that phase is exactly `UNINITIALIZED`. One Application-owned
pre-initialize transition makes them mutually exclusive with each other and
with `initialize`; completion leaves the phase unchanged and does not construct
a Workspace Runtime. A private client may then initialize the same Core and
discover the changed User package tree. The official `awesome skills` CLI is
one-shot and closes Core after its result. Neither path hot-updates an already
initialized Session's immutable Skill catalog.

After trusted activation, the backend publishes one frozen, slotted
`WorkspaceRuntime`. It is the request-visible snapshot of resolved
configuration and the composed Conversation, Turn, command, tool, model
catalog, context, extension, memory, MCP, Change Journal, and
`RuntimeResources`. Each request binds that object once and continues through
the same service graph, including awaited callbacks and foreground-owned child
tasks. A replacement is assembled entirely as a local candidate, validated,
checked against foreground Operation ownership, and published by one pointer
assignment. New requests then bind the new runtime; already admitted readers
finish against the old resource generation before it closes. Each generation's
`AsyncExitStack` owns reusable provider clients, an internally created Mem0
client, and MCP, and closes them exactly once in reverse order: MCP, Mem0, then
provider clients. Injected gateways and Mem0 clients are borrowed and never
registered for close. Startup recovery notification happens after publication
and cannot roll a ready runtime back. Candidate failure or cancellation closes
only candidate resources and leaves the previously published runtime untouched.
Provider and credential mutations use the same complete-candidate publication
path, without repeating startup recovery, and preserve the selected Thread.
The runtime's `model_catalog` is the frozen, provider-neutral `MODEL_CATALOG`;
credential presence, configured defaults, active selection, and region remain
dynamic Application/configuration state. Protocol v4 publishes those facts
separately as `model_catalog`, `provider_credentials`, and `model_identity`.
Foreground ownership, interactions, permission session, recovery delivery,
checkpoint saver, the process-owned Application SQLite worker, state leases,
and other process-lifetime resources remain in a separate Application
`AsyncExitStack` rather than the workspace snapshot. One bounded FIFO worker
thread owns the long-lived Application database connection; Application-facing
repositories expose async methods and never pass SQLite-owned values across
that boundary.

Application invocation diagnostics are also process/session owned and remain
outside `WorkspaceRuntime`. A bounded, nonblocking writer appends structured
JSON lines to `<AWESOME_HOME>/logs/application.jsonl`, rotating through
`application.jsonl.1` to `.4` at 5 MiB per file. It fails open so diagnostic I/O
cannot change an Application result. Records are constructed from an explicit
allowlist only: `version`, `timestamp`, `session_id`, `correlation_id`,
`operation`, `outcome`, `duration_ms`, and optional `error_code` and `usage`.
They never include prompts, model or tool bodies, queries, URLs, paths, secrets,
or arbitrary request/result payloads. The recorded outcome belongs to the
observed Application invocation; a successful request that starts background
Agent work is not a claim that the asynchronous Turn later completed
successfully.

A shared foreground arbiter grants one atomic lease to Agent Turns, direct
commands, state-changing commands, credential mutation, non-Tool interaction
resolution, or shutdown. Admission happens before Turn persistence. Read-only
snapshot commands are the explicit exception during an active Operation; a
pending interaction blocks new Operations and mutations, while matching Tool
approval continues the Operation that owns it. Shutdown closes admission,
cancels cancellable active work, waits for durable commits and leases to clean
up, retires runtime resources, then closes the checkpoint saver and Application
SQLite worker before releasing state leases.

### Agent Core and LangGraph

- **Responsibility:** `AgentState`, node routing, context/model/tool loop,
  message repair, compression, retry accounting, budgets, and finalization.
- **Does not own:** product Thread records, concrete storage wiring, or surface
  state.
- **Primary files:** `agent/state.py`, `agent/graph.py`, `agent/nodes.py`,
  `agent/budgets.py`, `agent/finalization.py`.
- **Dependencies:** provider-neutral Modeling, Core tools, and injected
  Agent-owned ports.

`PostAnswerFinalizer` is the only answer-finalization port visible to Agent.
Its strict request carries the already-generated answer, remaining model/retry
budgets, selected model, workspace identity, and ordered citations collected
from `tool_results`; identical repeated IDs collapse and conflicting values for
one ID are an invariant failure; more than 128 unique citations also fails
before the finalizer runs. A valid result may replace the answer with a
strip-nonblank value and reports at most one primary model call, its bounded
usage, and generic diagnostics. Active-time exhaustion supplies zero remaining
model calls without skipping a no-model finalizer. Agent revalidates the result
and budget before atomically applying the answer and accounting. Failure emits
a warning and preserves the existing answer and usage. Cancellation preserves
the prior checkpointed answer and immediately re-raises the original
cancellation without another Agent warning projection.

### Context Management

- **Responsibility:** deterministic prompt assembly, explicit path references,
  token estimates, Thread summaries, Skills, memory recall, and compression
  inputs.
- **Does not own:** graph routing or hidden persistence.
- **Primary files:** `context/builder.py`, `context/compression.py`,
  `context/path_refs.py`, `context/tokens.py`.

Compression may replace only the bounded base context. The active Turn's
assistant/tool tail is validated as one protocol chain, reserved in the input
budget, and appended exactly once with its pending-call and result indices
unchanged. Every emitted tool call receives one ordered observation before the
next model request, including deterministic non-executed observations for calls
skipped when a loop budget is exhausted.

### Model Gateway

- **Responsibility:** provider-neutral messages, tools, streaming events,
  errors, usage, the sole supported model catalog, model selection, retry
  reporting, and supported adapter calls.
- **Does not own:** tools, graph state, or product lifecycle.
- **Primary files:** `modeling/catalog.py`, `modeling/gateway.py`,
  `modeling/provider.py`, `modeling/turns.py`, `providers/factory.py`,
  `providers/deepseek.py`, `providers/kimi.py`.

The static directory is `MODEL_CATALOG -> ProviderDescriptor -> ModelProfile`.
It currently describes DeepSeek and Kimi, four models, their capabilities and
262,144-token context limits, Provider-local defaults, Kimi's `cn`/`global`
regions with `cn` as its default, and each Provider's `credential_id`.
Application/configuration still owns credential presence and runtime default,
model, and region selection. The catalog does not instantiate clients: the
concrete factory, adapters, and official endpoints remain in `providers/`.
Tavily selection and catalog concerns stay in the separate Web/configuration
boundary and never enter `ModelCatalog`. There is no generic provider registry,
DI container, or speculative third model Provider.

### Tool System

- **Responsibility:** tool registration, schemas, workspace/process policy,
  execution context, cancellation, timeouts, normalized failures, events, and
  bounded results.
- **Does not own:** model routing or surface prompts.
- **Primary files:** `core/tools/registry.py`, `core/tools/policy.py`,
  `core/tools/executor.py`, `core/tools/builtins/`.

The starting built-ins are `ls`, `read_file`, `write_file`, `edit_file`,
`delete`, `glob`, `grep`, and `execute`. This is an initial baseline, not a
maximum tool count.

### Conversation and Storage

- **Responsibility:** Thread, Turn, transcript, summary, tool activity, trust,
  ChangeSet metadata, checkpoint access, and SQLite transactions.
- **Does not own:** graph node transitions or TUI transcript state.
- **Primary files:** `conversation/models.py`, `conversation/service.py`,
  `storage/application_sqlite.py`, `storage/database.py`,
  `storage/compatibility.py`, `storage/state_lease.py`,
  `storage/state_recovery.py`, `storage/conversations.py`, and
  `storage/checkpoints.py`.

The resettable boundary is exactly `<AWESOME_HOME>/state`. Storage performs
atomic replacement under an exclusive lease; Application owns confirmation
and startup continuation; Protocol carries typed facts; Ink only presents and
routes the decision. Configuration, credentials, Skills, Memory, UI
preferences, and workspace files live outside that boundary and survive a
confirmed reset.

Thread search is an Application SQLite read ordered by `updated_at DESC, id
DESC`. Its opaque cursor binds the current Workspace and normalized query by
hash; the cursor never carries that workspace key in cleartext. The first implementation
uses literal substring matching rather than FTS or relevance ranking.

Schema 8 gives every Thread a nullable immediate-parent lineage. `/fork`
materializes the transcript prefix through one terminal Turn; `/retry`
materializes the prefix before one terminal Turn and starts a fresh Turn from
that Turn's user input while freezing its model, Thinking, Skill, and budget
configuration. Both create independent Thread, Entry, and Turn identities in
one Application SQLite transaction. They do not create a shared history DAG or
copy summaries, ToolActivity, checkpoints, or ChangeSets, and Retry neither
replays old tools nor undoes their effects.

Here, atomic replacement describes the filesystem namespace transition, not
revocation of arbitrary handles opened outside Awesome's lease protocol. An
open database handle prevents the rename on Windows. POSIX permits the rename
and unlink; such a pre-existing handle remains attached to the detached old
inode until it closes, while the canonical path names the fresh state.

### Change Journal

- **Responsibility:** controlled before/after snapshots, conflict detection,
  crash reconciliation, diff, undo, redo, and reversibility classification.
- **Does not own:** arbitrary host effects created by `execute`.
- **Primary files:** `core/filesystem.py`, `core/changes/filesystem.py`,
  `core/changes/journal.py`, `core/changes/operations.py`,
  `storage/changes.py`.

Undo and redo are multi-path restore transactions. They bind and preflight all
targets first, persist every pending intent before the first restore, apply the
restore through the same pinned workspace tree, then change the ChangeSet
lifecycle once. A failure before that lifecycle commit rolls already-applied
paths back while the original directory identities are still held; if that
rollback cannot be proven, pending evidence remains. Startup reconciliation
treats a committed operation as something to verify and finalize, and an
uncommitted operation as something to roll back completely; an identity,
content, or lifecycle conflict preserves the pending evidence instead of
guessing.

Each ordinary file mutation carries a durable mutation identity and distinct
before/after node types, so a directory, file, or symlink transition remains
representable after merge and through undo/redo recovery. Turn and direct
finalization reconcile only their own ChangeSet and release the in-memory owner
only after sealing succeeds. Schema 7 histories created before these optional
JSON fields remain readable; if a legacy record without mutation identity is
indistinguishable from an interrupted pending mutation, recovery preserves the
pending evidence and fails closed.

Workspace files and their diffs are the generated work product; there is no
parallel output object for ordinary file changes.

### Skills and MCP

- **Responsibility:** discover bundled/user/trusted-workspace Skills, validate
  and manage local User packages, assign and verify immutable session
  identities, load bounded instructions, connect configured MCP stdio servers,
  and adapt extension tools into the shared registry.
- **Does not own:** permissions or an alternate execution path.
- **Primary files:** `extensions/skills/discovery.py`,
  `extensions/skills/loader.py`, `extensions/skills/manifest.py`,
  `extensions/skills/package_manager.py`, `extensions/mcp/manager.py`,
  `extensions/mcp/adapter.py`.

Every effective Skill freezes a versioned package/`SKILL.md` identity. The
Turn manifest and checkpoint retain the identities selected by `auto` or a
named mode; `off` retains none. Model-visible Skill tools are filtered by that
frozen mode, and `context.read` hard admission verifies the operation and
identity before policy and again before content is returned. Workspace Skill
paths additionally revalidate the complete trusted-anchor chain without
following links or reparse points. One invalid package remains an isolated
diagnostic, and Runtime rebuild or package drift cannot widen a recovering
Turn's Skill scope.

Local package management accepts only validated directory or ZIP sources and
serializes operations across processes. A fresh install publishes a staged
package to an absent target with one same-directory no-replace atomic rename.
Replace and remove are marker-driven recoverable transactions, not single
atomic replacements: they quarantine the old target, roll back before
publication, and roll forward cleanup after publication. Caller cancellation
continues awaiting the owned worker until this transaction converges, without a
wall-clock cleanup deadline, and then re-raises cancellation.

MCP consumes the complete paginated catalog under page, tool-count, byte, and
deadline bounds, then compiles its JSON Schemas and complete namespaced tool
names before building every generation-bound Registry entry. While holding the
server lock, the Manager synchronously replaces the complete Registry namespace
and, without another `await`, publishes the matching client, catalog, generation,
and `CONNECTED` state. Publication is therefore all-or-none: `CONNECTED` proves
that the same generation's complete namespace is installed. References remain
local to one schema. Input arguments are validated before approval or remote
I/O; a declared
`outputSchema` validates `structuredContent`, and structured output without text
is rendered as bounded JSON. Restart removes the previous namespace first, and
timeout, disconnect, or cancellation invalidates the generation; calls never
lazily reconnect or replay an uncertain external action in the same Turn.

The compiler validates the complete `mcp.<server>.<tool>` name against the
strictest downstream 128-character limit before Registry publication. Invalid
names, schemas, duplicate names, or aggregate Registry limits close the new
client, invalidate the candidate generation, remove that server namespace, and
publish a sanitized `ERROR` state without exposing a valid subset.

### Memory

- **Responsibility:** independent local files (`USER.md`, workspace
  `MEMORY.md`) and optional Mem0 Cloud recall/distilled writes.
- **Does not own:** policy, trust, raw transcript upload, or provider routing.
- **Primary files:** `memory/local_file.py`, `memory/service.py`,
  `memory/mem0_cloud.py`, `memory/distiller.py`, `memory/finalization.py`.

Both memory layers are independently enabled and default off. Mem0 Cloud is the
only external memory adapter currently supported. `Mem0PostAnswerFinalizer`
lives here, translates Mem0-specific statuses and diagnostics into the generic
Agent contract, and currently returns the generated answer unchanged. Agent
does not import Memory or know Mem0 types.

### Protocol and Ink TUI

- **Responsibility:** versioned JSON-RPC requests, typed events, bounded NDJSON,
  terminal input, rendering, keyboard behavior, transcript projection, theme,
  clipboard, session-only pending input, and local presentation preferences.
- **Does not own:** models, LangGraph, tools, storage, Memory, Skills, or MCP.
- **Primary files:** `protocol/jsonrpc.py`, `protocol/stdio.py`,
  `tui/src/core/process.ts`, `tui/src/app/App.tsx`,
  `tui/src/app/submission-coordinator.ts`, and
  `tui/src/cli/startup-session-controller.ts`, `tui/src/cli/headless.ts`,
  `tui/src/cli/skills.ts`.

Two session-local controllers keep asynchronous sequencing out of the React
render tree without creating another state framework. `StartupSessionController`
continues typed trust, state-reset, and startup Thread-selection results; it
does not reproduce Application bootstrap phases. `SubmissionCoordinator`
owns one input's parse, Core-admission, optimistic identity, and generation
fence. The existing React queue still owns only pending presentation input,
while Core remains the sole foreground execution authority.

`awesome run` reuses that `ConnectedSurface`, startup controller, Protocol v4
client, Application facade, and durable Thread/Turn records without rendering
Ink. It creates a new Thread by default or targets one explicit Thread, then
projects only the durable final assistant entry as text or a versioned JSON
document. Parent stdout is result-only and stderr is diagnostic-only; Core
stdout remains private NDJSON. An unresolved interaction returns code 3, while
SIGINT sends urgent cancellation before returning 130 and closing the same
Core process tree. `--allow-network` can resolve only the exact active Turn's
`network.read` prompt as `allow_once`; it cannot create a Thread grant, resolve
another interaction, or bypass a hard denial.

`TerminalInput.tsx` is the only keyboard subscriber. A single discriminated UI
mode routes Enter, Escape, Tab, arrows, and global cancellation without
competing component listeners. Optimistic user messages are keyed by
`client_message_id`; Thread generations reject stale events after replacement.
The active Turn is one ordered Thinking/tool/answer timeline, and completed
answers use terminal Markdown rendering.

`/retry` returns one strict Protocol v4 `thread_retry` payload containing both
the authoritative Thread replacement and the accepted Operation. Because that
Operation's events may precede the response, `ConnectedSurface` temporarily
buffers the ordered event stream, installs the replacement, binds the new
generation to the returned Operation/Thread/Turn identities, and only then
replays the buffered events. The gate is capped at 1,024 events and 4 MiB of
encoded content. A capacity or identity violation is a fatal protocol error;
events are never projected onto the source Thread.

The stdio Host reads one bounded NDJSON stream but dispatches ordinary requests
as independent tasks. A fixed in-flight ceiling, bounded recent request-ID
history, and a bounded, deadline-protected stdout queue cap memory and stalled
consumer exposure. Initialize, interaction response, cancellation, and
shutdown remain control requests that bypass ordinary saturation. This wire
concurrency does not create a second mutation scheduler: the Application
foreground arbiter still serializes state-changing work.

Process lifetime has two owners. On POSIX, the TUI starts Core in its own
session/process group and termination targets that group. Each `execute` is
launched by a separate session supervisor whose lease pipe is owned by Core;
Core exit closes that lease and the supervisor terminates its remaining group.
On Windows, Core installs a kill-on-close lifetime Job Object and assigns
itself before asynchronous startup; failure to establish that invariant aborts
startup. Each `execute` also creates a nested kill-on-close Job Object and a
private supervisor that waits on an event. Core assigns that waiting supervisor
to the command job before releasing it to create the target, so the target and
all descendants inherit the command job without a spawn race. Root completion,
timeout, cancellation, or setup failure terminates that command job; normal or
abnormal Core exit remains covered by the outer lifetime job. The runner waits
for the root process independently from
stdout/stderr EOF, then gives inherited pipes a bounded drain phase; a
descendant holding a pipe open can truncate captured output but cannot keep the
Tool call pending forever.

The POSIX guarantee covers descendants that remain in the supervisor's session
and process group. A command that intentionally daemonizes with `setsid()` or a
similar escape is outside this cleanup boundary; neither platform mechanism is
an execution sandbox.

The TUI may queue at most three terminal inputs while the single Core Operation
is active. The queue is session-only and outside Thread Surface state: it
survives `/new`, `/resume`, `/fork`, and `/retry`, parses each head only when
promoted, executes FIFO, recalls the tail with Up when Composer is empty, and
treats queued `/quit` as a terminal barrier. It never becomes a Runtime,
protocol method, database record, or second execution authority.

### Safety

- **Responsibility:** workspace containment for file tools, sensitive-path
  rejection, identity-bound mutation, process-tree cleanup, explicit approval
  for Agent shell execution, command policy, redaction, and tool output bounds.
- **Primary files:** `core/filesystem.py`, `core/process_lifetime.py`,
  `core/tools/policy.py`, `core/tools/command_policy.py`,
  `core/tools/process.py`, `safety/redaction.py`.

Full access is an approval mode, not an isolation boundary. The current product
does not provide an operating-system sandbox; workspace trust, permissions,
and the command circuit breaker remain distinct policy layers above host
execution.

## Design Principles

1. Python Core is the only authority for product behavior.
2. Ink owns interaction and rendering only.
3. LangGraph owns graph execution, graph state, routes, and checkpoints.
4. Application owns product lifecycle without recreating graph execution.
5. Every tool follows one Registry, Policy, and Executor path.
6. Workspace files are the primary work result.
7. Execution is visible, cancellable, bounded, and recoverable where evidence
   is sufficient.
8. Product state uses embedded local storage under resolved Awesome paths.
9. Skills, MCP, Memory, and workspace instructions are untrusted context and
   cannot bypass tool policy.
10. A new abstraction needs a concrete second implementation or demonstrated
    product use.

## File Dependency Chain

The Python package graph is an explicit importer-to-allowed-dependency
contract. It is not a simple vertical DAG: adapters such as Storage implement
contracts owned by Agent, Conversation, Core, and Extensions, while Application
is the composition root and may depend on all concrete owners it wires.

| Importing package | May import these Awesome package roots |
| --- | --- |
| `agent` | `agent`, `core`, `modeling` |
| `application` | `agent`, `application`, `config`, `context`, `conversation`, `core`, `extensions`, `memory`, `modeling`, `paths`, `providers`, `safety`, `storage`, `version`, `web` |
| `config` | `config`, `core`, `modeling`, `paths` |
| `context` | `context`, `conversation`, `core`, `memory`, `modeling` |
| `conversation` | `config`, `conversation`, `core` |
| `core` | `core`, `safety` |
| `extensions` | `context`, `core`, `extensions` |
| `memory` | `agent`, `config`, `core`, `memory`, `modeling`, `paths`, `safety` |
| `modeling` | `modeling` |
| `protocol` | `application`, `core`, `paths`, `protocol`, `version` |
| `providers` | `config`, `modeling`, `providers` |
| `safety` | `modeling`, `safety` |
| `storage` | `agent`, `config`, `conversation`, `core`, `extensions`, `storage` |
| `web` | `core`, `web` |

`tests/structural/test_dependency_architecture.py` is the executable source for
this exact adjacency table and for external-framework ownership. The TUI is a
separate TypeScript process and reaches Python only through Protocol v4.
The `memory` -> `agent` row is intentionally narrower than its package-level
appearance: only `memory/finalization.py` may import `agent/finalization.py`.

The package dependency arrow is therefore `config -> modeling`, never
`modeling -> config`. Application composes the static model directory with
dynamic configuration and credential state, then publishes that catalog
through Protocol v4 so Ink does not copy model or Provider enumerations.

Concrete providers and storage adapters are wired in
`application/composition.py`. The Agent imports provider-neutral contracts, and
the protocol imports the Application facade rather than individual subsystems.

## State Ownership

| State | Owner | Location | Lifetime |
| --- | --- | --- | --- |
| Workspace trust | Application Storage | `state/application.db` | until user data removal |
| Threads, immediate-parent lineage, Turns, transcript, summaries | Conversation + Storage | `state/application.db` | durable local history |
| Tool activity summaries | Storage | `state/application.db` | bounded local history |
| Agent graph channels | LangGraph | `state/checkpoints.db` | unfinished Turn only |
| ChangeSet metadata | Change Journal + Storage | `state/application.db` | durable local history |
| Change blobs | Change Journal | `state/change-journal/` | while referenced |
| Provider model transaction intent | Application + Config | `state/provider-model-transaction.json` | until verified reconciliation |
| User memory | Memory | `memory/USER.md` | user controlled |
| Workspace memory | Memory | `workspaces/<key>/MEMORY.md` | workspace scoped |
| Cloud facts | Mem0 Cloud | external account | only when enabled |
| UI preferences | Ink TUI | `ui.json` | user controlled |
| Application invocation diagnostics | Application process/session | `logs/application.jsonl{,.1,.2,.3,.4}` | bounded local operational history |
| Workspace files | user and tools | workspace | primary project state |

Thread exports are ordinary workspace files, not a second copy of product
state. Their deterministic public projection excludes workspace keys and
internal metadata; a changed export is journaled and can be undone like other
controlled file writes. Path and output bounds are checked before mutation. A
failed attempt with no reconciled mutation evidence does not publish an empty
ChangeSet; if bytes did land, recovery retains the actual evidence.

Token deltas, spinners, raw provider payloads, unbounded shell output, and
credentials are not stored as product history. Tool observations required for
an unfinished Turn remain in the LangGraph checkpoint, including any citations
inside serialized `tool_results` and the derived ordered `citations` snapshot.
User-facing activity history stores bounded summaries.

## Error, Cancellation, and Recovery

- Expected tool failures become normalized observations that the model can
  address within remaining budgets.
- Unexpected tool or graph failures terminate the Turn with a stable product
  error and visible event.
- Provider adapters classify errors and report retry usage; the Agent enforces
  configured retry and model-call limits.
- Cancellation propagates through the foreground operation, model call, and
  tool execution. Application marks the Turn cancelled, then keeps the
  foreground lease until local activity, transcript, ChangeSet, and checkpoint
  cleanup reaches a known result. Cleanup failure preserves the primary
  cancelled fact; startup reconciliation retries stale terminal checkpoint
  evidence. Only process cleanup and best-effort event delivery are bounded.
- A terminal event permits the TUI to promote one pending input. Typed busy
  races requeue the same identity at the head without duplicate failure text.
- TUI cancellation and interaction controllers release completed request
  identity before the next Operation or Interaction. Nonfatal failures remain
  visible and retryable; Core exit is fatal and disables Composer input.
- Graph checkpoints are keyed by Turn ID. Application product records reference
  the same key without copying graph channels.
- Startup recovery acts only on evidence in product records and checkpoints.
  Uncertain external side effects require a user decision instead of automatic
  replay.
- Context compression, message repair, budget exhaustion, and finalization are
  Agent invariants rather than optional middleware.
- Optional post-answer finalization is isolated behind the Agent-owned port. A
  cancelled, failed, invalid, or over-budget implementation cannot overwrite
  the already-generated answer; cancellation still propagates, while a valid
  result is charged before completion.

## Extension Points

Current extension points are deliberately narrow:

- a supported model or Provider is added to the static model catalog and the
  concrete adapter factory, then composed at the Application boundary; this is
  not a runtime registry or a reason to invent a third Provider;
- new built-in or MCP tools enter the existing Registry/Policy/Executor path;
- new Skills follow the current manifest schema and trusted discovery order;
- a second external memory service must justify a shared provider abstraction;
- a future surface adapts `ApplicationFacade` and typed events instead of
  reimplementing Core behavior.

The product roadmap also identifies Multi-Agent delegation, search tools, Cron
tasks, Gateway messaging, and an optional Docker tool backend. These are future
capabilities, not components in the current-system diagram. Local one-command
User Skill package management is already part of the current Application and
CLI boundaries. A Docker backend would sit below Tool Executor policy; it would
not replace workspace trust.
