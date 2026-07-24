# Awesome Architecture

Awesome is a terminal AI coding assistant. One `awesome` launcher starts an Ink
interface and a private Python process; all product behavior remains in the
Python Core, while the TUI submits intent and renders typed events.

This document is the authoritative technical overview. Focused documents under
[`docs/architecture/`](docs/architecture/README.md) explain individual
boundaries without redefining the system.

## System Overview

```text
┌───────────────────────────────────────────────────────────────────────────┐
│                         Entry & Presentation                              │
│                                                                           │
│  awesome launcher                     Ink + React TUI                     │
│  CLI arguments                        Input / Rendering / Keyboard / UX   │
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
│   │   └── workspace/  # workspace identity and trust models
│   ├── extensions/
│   │   ├── mcp/        # MCP stdio client and tool adapter
│   │   └── skills/     # Skill discovery, loading, and tool exposure
│   ├── memory/         # USER.md, MEMORY.md, Mem0 Cloud, memory tools
│   ├── modeling/       # provider-neutral messages and model gateway
│   ├── protocol/       # JSON-RPC types and private stdio Host
│   ├── providers/      # DeepSeek and Kimi adapters
│   ├── safety/         # redaction helpers
│   ├── storage/        # embedded SQLite and checkpoint adapters
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

The root `AGENTS.md` is an immutable session source loaded only after trust. A
bounded identity-checked read either supplies the whole mandatory instruction
source or supplies no content plus a structured diagnostic; it is never
meaning-changing truncation. That diagnostic does not invalidate configuration.

Application state preflight is read-only and runs before trust, checkpoints,
or writable storage. The current format is Schema 7. Product and schema
versions are independent: schema identity changes only with persisted
semantics and increases monotonically. Older state can be reset only through
the typed startup interaction; newer, unknown, corrupt, unreadable, or locked
state is never silently deleted.

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
the assistant transcript entry, records bounded usage, seals its ChangeSet,
deletes the finished checkpoint, and emits completion events.

### Tool call

```text
Model ToolCall
    -> ToolRegistry lookup
    -> schema validation
    -> workspace and command policy
    -> ToolExecutor timeout/cancellation/event envelope
    -> built-in or MCP adapter
    -> normalized ToolResult
    -> bounded activity summary + Agent observation
```

File-changing built-ins write through the Change Journal. `execute` runs on the
host and is not a sandbox. Request approval asks before writes, deletes, shell,
MCP, and unknown capabilities. Accept edits allows ordinary workspace writes
only. Confirmed Full access allows known built-in local writes, deletes, and
shell for its bound Thread; MCP and unknown capabilities still ask. Hard
denials always run first for Agent and direct `!` commands. The dialect-aware
command circuit breaker is designed to stop recognizable accidents, not to
detect arbitrary hostile obfuscation. Shell effects may escape the workspace
and cannot be reversed by the journal.

### Slash command

```text
/command
   ├── Ink-owned presentation command -> local UI state
   ├── Application command -> command.execute -> typed result
   └── Skill command -> shared Skill/Application boundary
```

The authoritative Core command path is:

```text
Ink command controller
    -> Protocol v3 command.execute
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
Protocol v3 discriminated outcomes that TypeScript validates and presents
exhaustively.
Command progress is pending Surface lifecycle state, not a second durable
operation model.

### Resume and recovery

`--continue` selects the most recent workspace Thread; `--resume` selects a
specific Thread. On startup, Application reconciles unfinished product Turns
with LangGraph checkpoints:

- a completed graph state is finalized into product records;
- a valid unfinished checkpoint is resumable;
- a missing or corrupt checkpoint fails the Turn with a stable error code;
- an uncertain shell or MCP side effect requires an explicit retry/abort
  interaction;
- checkpoints left behind by terminal product Turns are removed.

## Major Subsystems

### Application Host

- **Responsibility:** workspace initialization, configuration resolution,
  Thread/Turn lifecycle, commands, foreground operation serialization,
  interactions, cancellation, event projection, recovery, and composition.
- **Does not own:** model reasoning, graph routing, tool implementation, or UI
  rendering.
- **Primary files:** `application/facade.py`, `application/composition.py`,
  `application/turns.py`, `application/operations.py`.
- **Dependencies:** Agent Core, current adapters, Conversation, Storage, Core,
  Context, Extensions, and Memory.

A shared foreground arbiter grants one atomic lease to Agent Turns, direct
commands, state-changing commands, credential mutation, non-Tool interaction
resolution, or shutdown. Admission happens before Turn persistence. Read-only
snapshot commands are the explicit exception during an active Operation; a
pending interaction blocks new Operations and mutations, while matching Tool
approval continues the Operation that owns it. Shutdown closes admission,
cancels active work, and waits for leases to clean up before closing processes
or databases.

### Agent Core and LangGraph

- **Responsibility:** `AgentState`, node routing, context/model/tool loop,
  message repair, compression, retry accounting, budgets, and finalization.
- **Does not own:** product Thread records, concrete storage wiring, or surface
  state.
- **Primary files:** `agent/state.py`, `agent/graph.py`, `agent/nodes.py`,
  `agent/budgets.py`.
- **Dependencies:** provider-neutral Modeling, Core tools, and injected Memory
  services.

### Context Management

- **Responsibility:** deterministic prompt assembly, explicit path references,
  token estimates, Thread summaries, Skills, memory recall, and compression
  inputs.
- **Does not own:** graph routing or hidden persistence.
- **Primary files:** `context/builder.py`, `context/compression.py`,
  `context/path_refs.py`, `context/tokens.py`.

### Model Gateway

- **Responsibility:** provider-neutral messages, tools, streaming events,
  errors, usage, model selection, retry reporting, and supported adapter calls.
- **Does not own:** tools, graph state, or product lifecycle.
- **Primary files:** `modeling/gateway.py`, `modeling/provider.py`,
  `modeling/turns.py`, `providers/deepseek.py`, `providers/kimi.py`.

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
  `storage/database.py`, `storage/compatibility.py`, `storage/state_lease.py`,
  `storage/state_recovery.py`, `storage/conversations.py`,
  `storage/checkpoints.py`.

The resettable boundary is exactly `<AWESOME_HOME>/state`. Storage performs
atomic replacement under an exclusive lease; Application owns confirmation
and startup continuation; Protocol carries typed facts; Ink only presents and
routes the decision. Configuration, credentials, Skills, Memory, UI
preferences, and workspace files live outside that boundary and survive a
confirmed reset.

### Change Journal

- **Responsibility:** controlled before/after snapshots, conflict detection,
  diff, undo, redo, and reversibility classification.
- **Does not own:** arbitrary host effects created by `execute`.
- **Primary files:** `core/changes/journal.py`, `core/changes/operations.py`,
  `storage/changes.py`.

Workspace files and their diffs are the generated work product; there is no
parallel output object for ordinary file changes.

### Skills and MCP

- **Responsibility:** discover trusted bundled/user/workspace Skills, load
  bounded instructions, connect configured MCP stdio servers, and adapt MCP
  tools into the shared registry.
- **Does not own:** permissions or an alternate execution path.
- **Primary files:** `extensions/skills/discovery.py`,
  `extensions/skills/loader.py`, `extensions/mcp/manager.py`,
  `extensions/mcp/adapter.py`.

Workspace Skill paths and opened identities are revalidated without following
links or reparse points; one invalid package remains an isolated diagnostic.
MCP compiles a server's complete bounded JSON Schema catalog before atomically
publishing its client, generation, namespace, and `CONNECTED` state. References
remain local to one schema. Restart removes the previous namespace first, and
timeout, disconnect, or cancellation invalidates the generation; calls never
lazily reconnect or replay an uncertain external action in the same Turn.

### Memory

- **Responsibility:** independent local files (`USER.md`, workspace
  `MEMORY.md`) and optional Mem0 Cloud recall/distilled writes.
- **Does not own:** policy, trust, raw transcript upload, or provider routing.
- **Primary files:** `memory/local_file.py`, `memory/service.py`,
  `memory/mem0_cloud.py`, `memory/distiller.py`.

Both memory layers are independently enabled and default off. Mem0 Cloud is the
only external memory adapter currently supported.

### Protocol and Ink TUI

- **Responsibility:** versioned JSON-RPC requests, typed events, bounded NDJSON,
  terminal input, rendering, keyboard behavior, transcript projection, theme,
  clipboard, session-only pending input, and local presentation preferences.
- **Does not own:** models, LangGraph, tools, storage, Memory, Skills, or MCP.
- **Primary files:** `protocol/jsonrpc.py`, `protocol/stdio.py`,
  `tui/src/core/process.ts`, `tui/src/app/App.tsx`.

`TerminalInput.tsx` is the only keyboard subscriber. A single discriminated UI
mode routes Enter, Escape, Tab, arrows, and global cancellation without
competing component listeners. Optimistic user messages are keyed by
`client_message_id`; Thread generations reject stale events after replacement.
The active Turn is one ordered Thinking/tool/answer timeline, and completed
answers use terminal Markdown rendering.

The TUI may queue at most three terminal inputs while the single Core Operation
is active. The queue is session-only and outside Thread Surface state: it
survives `/new` and `/resume`, parses each head only when promoted, executes
FIFO, recalls the tail with Up when Composer is empty, and treats queued
`/quit` as a terminal barrier. It never becomes a Runtime, protocol method,
database record, or second execution authority.

### Safety

- **Responsibility:** workspace containment for file tools, sensitive-path
  rejection, explicit approval for Agent shell execution, command policy,
  redaction, and tool output bounds.
- **Primary files:** `core/tools/policy.py`, `core/tools/command_policy.py`,
  `safety/redaction.py`.

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

```text
core contracts / workspace / events
                 │
                 ▼
 modeling     conversation      config
     │             │              │
     ├─────────────┼──────────────┤
     ▼             ▼              ▼
 providers       storage      extensions / memory
          \         │         /
           \        │        /
            ▼       ▼       ▼
              context + agent
                     │
                     ▼
                application
                     │
                     ▼
             protocol / stdio Host
                     │
                     ▼
                Ink + React TUI
```

This is the architectural dependency direction, not a claim that every Python
file imports the layer immediately above it. Structural tests maintain the
current allowed package edges and framework owners.

Concrete providers and storage adapters are wired in
`application/composition.py`. The Agent imports provider-neutral contracts, and
the protocol imports the Application facade rather than individual subsystems.

## State Ownership

| State | Owner | Location | Lifetime |
| --- | --- | --- | --- |
| Workspace trust | Application Storage | `state/application.db` | until user data removal |
| Threads, Turns, transcript, summaries | Conversation + Storage | `state/application.db` | durable local history |
| Tool activity summaries | Storage | `state/application.db` | bounded local history |
| Agent graph channels | LangGraph | `state/checkpoints.db` | unfinished Turn only |
| ChangeSet metadata | Change Journal + Storage | `state/application.db` | durable local history |
| Change blobs | Change Journal | `state/change-journal/` | while referenced |
| User memory | Memory | `memory/USER.md` | user controlled |
| Workspace memory | Memory | `workspaces/<key>/MEMORY.md` | workspace scoped |
| Cloud facts | Mem0 Cloud | external account | only when enabled |
| UI preferences | Ink TUI | `ui.json` | user controlled |
| Workspace files | user and tools | workspace | primary project state |

Token deltas, spinners, raw provider payloads, unbounded shell output, and
credentials are not stored as product history. Tool observations required for
an unfinished Turn remain in the LangGraph checkpoint; user-facing activity
history stores bounded summaries.

## Error, Cancellation, and Recovery

- Expected tool failures become normalized observations that the model can
  address within remaining budgets.
- Unexpected tool or graph failures terminate the Turn with a stable product
  error and visible event.
- Provider adapters classify errors and report retry usage; the Agent enforces
  configured retry and model-call limits.
- Cancellation propagates through the foreground operation, model call, and
  tool execution. Application marks the Turn cancelled, seals known changes,
  and removes its checkpoint.
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

## Extension Points

Current extension points are deliberately narrow:

- new model adapters implement the existing provider contract and are composed
  at the Application boundary;
- new built-in or MCP tools enter the existing Registry/Policy/Executor path;
- new Skills follow the current manifest schema and trusted discovery order;
- a second external memory service must justify a shared provider abstraction;
- a future surface adapts `ApplicationFacade` and typed events instead of
  reimplementing Core behavior.

The product roadmap also identifies documentation tooling, one-command Skills
installation, Multi-Agent delegation, search tools, Cron tasks, Gateway
messaging, and an optional Docker tool backend. These are future capabilities,
not components in the current-system diagram. A Docker backend would sit below
Tool Executor policy; it would not replace workspace trust.
