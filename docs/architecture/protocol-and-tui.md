# Protocol and TUI

The Ink + React package is Awesome's terminal presentation surface. It owns
input, layout, transient UI modes, transcript projection, theme, clipboard,
and Core process lifecycle. It does not own model calls, graph execution,
tools, product persistence, Memory, Skills, or MCP.

One private `awesome-core` process exposes the Application facade over JSON-RPC
2.0 messages framed as newline-delimited JSON on stdio. Core stdout is protocol
only; logs use stderr.

The protocol consumes typed user intent and control requests and outputs typed
Application results plus ordered event facts. The TUI converts those facts into
transient and rendered state. Neither boundary accepts arbitrary Python objects
or lets presentation state flow back as product authority.

## Why a private protocol

Separating Node presentation from Python behavior allows each language to use
its strongest ecosystem without duplicating product authority. A versioned,
strict protocol makes process crashes, stale clients, and cross-language type
drift explicit.

The protocol is not a public network API. It assumes one launcher-owned local
peer, but still validates every request and applies explicit input/backpressure
bounds because malformed or mismatched local components must not corrupt state.
Both inbound and outbound frames enforce the same strict 1 MiB UTF-8 JSON
boundary, as described below.

## Protocol v5 contract

Initialization requires literal `protocol_version: 5`, client name `awesome`,
and the same product version as Core. A v3 client fails explicitly even if its
product version matches. Protocol and product versions answer different
questions: wire compatibility versus release identity.

The numeric contract identity is not copied manually between the two
processes. `contract-versions.json` generates dependency-free literal bindings
in `src/awesome_agent/contract_versions.py` and
`tui/src/contract-versions.ts`; runtime code imports those bindings and never
loads the catalog. `VERSION` remains the separate product-version owner. A
release combines both sources into its artifact-only `compatibility.json`
tuple instead of forcing their numbers to match.

Request IDs are JSON/JavaScript-safe integers or 1–128 Unicode-scalar strings
without unpaired UTF-16 surrogates. Numeric IDs must be integral, finite,
non-Boolean, and within `-(2^53 - 1)..2^53 - 1`. Optional fields are omitted
when absent; explicit `null` is accepted only by a schema that declares it
nullable.

The same safe-integer boundary applies recursively to integer-valued numbers in
all results, errors, events, and generic JSON values. The Core writer also
rejects non-finite numbers, invalid Unicode, non-string object keys, non-JSON
containers, and output nesting beyond 64 levels before serialization. This is
the final invariant boundary even when an upstream producer returns an
unconstrained Python object.

The current request methods are:

| Method | Purpose |
| --- | --- |
| `initialize` | negotiate identity and perform startup/bootstrap |
| `skill.list` | list valid installed User Skill packages before initialization |
| `skill.install` | validate and install or replace one local User Skill package before initialization |
| `skill.remove` | remove one installed User Skill package before initialization |
| `application.getState` | read authoritative Application state |
| `thread.list` | page workspace Threads |
| `thread.search` | page workspace-isolated conversation substring matches |
| `thread.read` | page one Thread and transcript projection |
| `turn.submit` | admit natural-language foreground work |
| `direct.execute` | admit a direct shell Operation |
| `command.execute` | execute a Core-owned slash command |
| `provider.credential.set` | add, replace, or delete a selected credential source |
| `interaction.respond` | resolve one typed pending interaction |
| `operation.cancel` | cancel one active Operation ID |
| `shutdown` | close admission and cleanly stop Core |

Core sends `event` notifications containing a strict envelope. Event families
cover Operation and Turn lifecycle, assistant text/reasoning deltas, provider
retry, tool lifecycle, context preparation/compression, usage, Memory status,
interactions, and warnings.

`thread.search` deliberately reuses `ThreadListResult`, so surfaces need no
parallel Thread-card model. The query is trimmed and bounded before Application
SQLite performs literal substring matching under a 5,000,000 VM-op scan budget;
the opaque cursor binds both the active Workspace and query hash and keeps RPC
keyset pagination available. `/search` reuses the generic selection
continuation but presents only the 50 newest matches, with a prompt to refine
the query when more exist. An exhausted scan returns `result_too_large`.
`/export` adds only a strict `thread_export` result whose path is 1–1,000
characters and whose nonempty `change_set_id` is present exactly when bytes
changed; the exhaustive presenter does not read raw files or private metadata.

`/fork` reuses `thread_transition` with reason `fork`; `/retry` uses one strict
combined `thread_retry` payload containing both a retry transition and its
accepted Operation. Every Thread projection carries required nullable lineage,
so root, fork, and retry identities remain explicit across Python, fixtures,
Zod, effects, and hydration without adding another RPC or surface model.

## Cross-language evidence

Python owns serialization of method results, `CommandOutcome` variants, and
events. `scripts/generate_protocol_fixtures.py` writes deterministic valid and
invalid fixtures under `protocol/fixtures/v5/`. TypeScript Zod schemas validate
the same corpus.

```text
contract-versions.json
  -> generated Python + TypeScript literal bindings
Python Pydantic contracts
  -> generated v5 fixtures + manifest hashes
  -> TypeScript strict Zod schemas
  -> protocol contract tests
  -> exhaustive reducers/presenters
```

There is no generic JSON fallback. Adding a payload variant must update the
Python type, fixture generator, TypeScript schema, reducer/effect when needed,
Presenter, and tests. TypeScript exhaustive switches then expose missing cases
at compile time.

## Handshake state machine

`LocalApplication` owns the only `ApplicationBootstrap` and the only mutable
`BootstrapPhase`. The state machine is therefore an Application lifecycle
component, not stdio Host state:

```text
UNINITIALIZED
  -> skill.* starts: PREINITIALIZE_ACTIVE
  -> initialize starts: INITIALIZING

PREINITIALIZE_ACTIVE (guard; BootstrapPhase remains UNINITIALIZED)
  -> result/error/cancellation after the owned worker converges: UNINITIALIZED
  -> another skill.* or initialize: reject

INITIALIZING
  -> ready result: READY
  -> trust_required result: TRUST_REQUIRED
  -> state_reset_required result: STATE_RESET_REQUIRED
  -> failure/cancellation: previous phase

TRUST_REQUIRED
  -> matching trust accepted after activation: READY

STATE_RESET_REQUIRED
  -> matching reset accepted: remain non-ready
  -> initialize again
```

Before dispatch, the Host maps the method to the closed
`ApplicationOperation` set and asks Application for an admission decision. It
only translates a rejection to the wire; it does not maintain another phase
enum or inspect serialized request/result payloads to advance readiness.

The three private `skill.*` methods are the only ordinary-looking methods
available at plain `UNINITIALIZED`. They share one Application-owned
pre-initialize guard, are mutually exclusive with one another and with
`initialize`, and never initialize Application themselves. After one completes,
a private client may initialize the same Core and discovery observes the changed
User package tree. The official `awesome skills` CLI instead performs one
request and closes Core. Neither path hot-updates an already initialized
Session's immutable catalog.

Before `READY`, all other ordinary requests receive a stable
server-not-initialized or server-not-ready error. Once initialization starts,
`skill.*` is no longer admitted. A second concurrent initialize receives
`initialization_in_progress`; the matching bootstrap interaction, cancellation,
and shutdown retain their defined control paths. A malformed or v3 initialize
never advances the Application phase.

Initialization remains repeatable after `READY` so a surface retry can observe
the current snapshot without creating a second Application. These ownership
rules preserve one Application-owned Protocol v5 request, result, status, and error shape.

## Framing and dispatch bounds

Core limits each request line to 1 MiB. The TUI limits both encoded requests and
decoded Core frames to the same size. Core's output writer serializes one whole
line and checks its compact UTF-8 byte count before writing. Core reads one
sequential byte stream, while accepted ordinary requests run in independent
tasks. This prevents a slow provider or command from blocking urgent control
parsing.

| Resource | Bound |
| --- | ---: |
| Core request line | 1 MiB |
| TUI encoded/decoded frame | 1 MiB |
| Core output frame | 1 MiB compact UTF-8 content |
| Core output JSON depth | 64 levels |
| ordinary in-flight requests | 128 |
| background control requests | 16 |
| active/recent request IDs | 4,096 |
| stdout writer queue | 64 messages |
| stdout write deadline | 5 seconds |

`initialize` and `interaction.respond` use the background control lane.
`operation.cancel` and `shutdown` are urgent and bypass both ceilings. A
shutdown request must first pass normal JSON-RPC shape and ID validation;
invalid input cannot cancel legal work.

Overloaded requests receive a stable busy error. Notifications have no
response, so overloaded ordinary notifications are dropped. Active and recent
IDs reject duplicate work without keeping unbounded history.

All responses and events share one writer lock and one bounded queue. Each line
is serialized whole, so concurrently completed requests cannot interleave JSON
bytes. A blocked consumer or write failure closes the transport path rather
than accumulating unbounded memory.

For Turn and Direct admission, `operation.started` is enqueued before the
Application can return `OperationAccepted`; later lifecycle events may then
race the matching response. The TUI installs the event consumer before issuing
requests and treats event correlation IDs as authoritative, so early events do
not depend on response-first buffering.

Retry adds one local ordering gate because its events name a Thread the surface
cannot install until the combined response arrives. `ConnectedSurface` opens
the gate before `command.execute`, buffers the event stream in sequence, and
releases it only after the authoritative retry transition has incremented the
Thread generation and the returned Operation identity has been bound to that
generation. The local gate is capped at 1,024 events and 4 MiB of encoded
content. A capacity or Operation/Thread/Turn identity violation is fatal
protocol desynchronization. A rejected retry replays valid source-Thread
events instead of dropping them.

`thread.read` first shrinks its page under its application byte budget. The
writer is the final invariant boundary for every method and event: if any
request result still exceeds 1 MiB, Core sends a bounded `result_too_large`
Application failure with the same request ID. It is non-retryable because the
method may already have produced an external effect; the protocol never
transparently replays it. Event schemas are independently bounded, and an
oversized event is rejected before stdout receives a partial or invalid frame.

Wire concurrency is not mutation concurrency. The Application foreground
arbiter still decides which Turn, direct command, state mutation, interaction,
or shutdown may run.

## Semantic presentation pipeline

Core sends facts, not preformatted terminal widgets:

```text
Application fact
  -> Protocol v5 payload
  -> optional authoritative Surface effect
  -> exhaustive presentCommandPayload()
  -> CommandPresentation
  -> shared terminal components
  -> transcript
```

Effects and Presenters are separate. An effect can install an authoritative
Thread replacement or title, but it cannot format output. A Presenter maps one
typed payload to rows, notices, panels, or an empty state without mutating
product state.

Shared components own borders, wrapping, alignment, symbols, and semantic
colors. They do not accept arbitrary records for stringification. This costs
more code for a new command but prevents accidental leakage of internal JSON or
secrets.

Protocol v5 makes citations part of the same durable projection rather than a
parallel event stream. Assistant-entry metadata carries ordered strict
`Citation` values; transcript hydration preserves them, reconciliation rejects
stale replacements, and `BlockView` links only markers whose IDs exist in that
entry's catalog. Unknown `[[S...]]` markers remain plain text. Finalization has
already appended a bounded Sources section when Web was used without any valid
marker.

## Surface state versus presentation state

`SurfaceState` is a projection of Core facts:

- connection and fatal state;
- current Application and Thread snapshot;
- Thread generation and event sequence;
- active Operation/Turn timeline;
- pending interaction, usage, warnings, and committed transcript.

Theme, composer buffer/history, cursor metrics, expanded details, picker
selection, secret-entry text, and pending-input queue are presentation state.
They do not enter the protocol or product database.

The distinction prevents restoring a Thread from also restoring stale UI
controls. A new surface can adapt the same facade/events while choosing a
different presentation model.

## Session-local orchestration

Two concrete controllers keep request sequencing out of large React
components without introducing a global store or a second product runtime:

- `StartupSessionController` binds one connected Surface and launch intent. It
  continues typed trust, state-reset, and startup Thread-selection outcomes by
  calling the existing startup protocol functions. It does not own or infer
  Application bootstrap phases.
- `SubmissionCoordinator` owns the transaction for one promoted terminal
  input: parse at promotion time, capture the Thread generation, correlate an
  optimistic `client_message_id`, request Core admission, and reject late
  projection after a Thread replacement.

Composer history, modal selection, notices, and the pending-input queue remain
React presentation state. The coordinator never drains input independently;
Core still admits the single foreground Operation.

## Headless surface

`awesome run` is a second presentation mode of the same TypeScript launcher,
not a second product runtime. It connects one `ConnectedSurface`, invokes the
same `beginStartup` flow through `StartupSessionController`, selects or creates
a Thread through existing commands, submits through Protocol v5, and hydrates
the durable final assistant entry with `thread.read`. Python Application remains
the only lifecycle and mutation authority.

The runner does not render Ink or consume terminal input. Parent stdout is
reserved for one successful final text value or one versioned JSON document;
diagnostics use parent stderr. Core child stdout remains private NDJSON and is
never forwarded as command output. This separation makes redirected output
deterministic while reusing Protocol v5.

JSON output version 2 contains the durable text, ordered `citations`,
`usage.web_requests`, IDs, termination reason, and the other usage counters.
Text mode renders the already-finalized answer, including any Sources section.

An unresolved interaction is a terminal headless outcome: the runner requests
cancellation of an admitted Operation and returns code 3 instead of inventing
an approval. SIGINT follows the same urgent `operation.cancel` method, makes a
bounded cancellation attempt, suppresses result output, and returns 130. The
launcher then performs bounded Surface/Core shutdown, including forced
process-tree termination when graceful close cannot complete.

The parsed `--allow-network` value is process-local authority for one exact
`network.read` interaction belonging to the active headless Turn. It resolves
that prompt as `allow_once`; it cannot create a Thread grant or bypass denial.

## Input ownership

`TerminalInput.tsx` is the only Ink `useInput` subscriber. One root key router
dispatches keys to a discriminated UI mode:

- Composer;
- command menu;
- picker;
- secret input;
- approval;
- workspace trust or state reset;
- fatal flow.

Exactly one mode owns Enter, Escape, Tab, arrows, and global cancellation.
Components render the selected state; they do not install competing keyboard
listeners. This avoids double submission and mode-dependent race bugs.

The command menu is a Composer accessory, so the draft remains visible and its
cursor stays active. Picker, approval, trust, secret, and fatal modes are
exclusive and hide the Composer cursor.

## Terminal cursor and layout

Composer uses Ink's physical cursor, not a printed block glyph:

```text
grapheme-aware logical cursor
  -> display-width-aware viewport row/column
  -> current Ink Yoga layout in the Composer insertion phase
  -> React TerminalFrameMetrics
  -> InkCursorBridge fullscreen adjustment
  -> ancestor useCursor physical terminal position
```

`TerminalSurfaceLayout` owns `useCursor`. On every commit with an active
Composer, the descendant insertion effect recomputes the current Ink Yoga
layout after host mutations and publishes that position before the ancestor
cursor effect runs. Streaming content and its cursor therefore come from the
same layout; cached `useBoxMetrics` coordinates only gate initial readiness and
request the first ancestor cursor commit, and never position a current frame.

The bridge also isolates an Ink 7.1 fullscreen convention where a frame
filling the viewport omits a trailing newline. The synchronous layout pass adds
one extra Yoga calculation while the Composer is active, but preserves natural
terminal flow and terminal-host IME rendering. Terminal frame metrics remain
local presentation state. A future Ink upgrade can remove either workaround
only after per-frame cursor regressions below, equal to, and above the viewport,
plus resize and Composer remount regressions, still pass.

IME preedit remains a responsibility of the terminal host. Composer logic
operates on submitted grapheme input rather than attempting to render platform
IME state itself.

The natural terminal flow is Welcome, committed transcript, active Turn,
pending inputs, notices, command menu, Composer or exclusive interaction, then
status. The current Thread is dynamic React state rather than permanently
printed terminal output.

## Pending input queue

While one Core Operation is active, the TUI may hold at most three submitted
terminal inputs. The queue is deliberately session-only:

- items execute FIFO;
- each head is parsed only when promoted;
- an empty Composer can recall the tail with Up;
- a picker or approval pauses promotion;
- `/new`, `/resume`, `/fork`, and `/retry` change which Thread receives the
  following item;
- queued `/quit` is an ordered terminal barrier;
- a retryable busy race requeues the same identity at the head.

The queue does not become a Runtime, protocol method, Thread record, or second
execution authority. Core continues to admit one foreground action.

## Transcript and event reconciliation

The active Turn is one ordered timeline of Thinking, tool activity, and
assistant output. Deltas update live projections; completed answers use terminal
Markdown. Tool events carry semantic verb, target, outcome, summary, optional
detail, duration, and error code.

`client_message_id` reconciles optimistic user input with accepted durable
entries. Thread replacement increments a generation, clears the active frame,
installs the authoritative Application/Thread snapshot, and rejects late events
from the previous generation. Event sequence detects duplicates and gaps.

For retry, replacement is installed before any buffered event is projected.
The accepted Operation's start and later deltas therefore enter the new
generation even when they arrived on stdio before the command response; they
can never appear on the source Thread.

After reconnect or resume, `thread.read` is the durable source. Live projections
are merged by stable identity instead of appended blindly, preventing duplicate
messages when an event and hydrated transcript describe the same fact.

Nonfatal request failures remain visible in the current transcript and leave
Composer usable. A malformed Core event, broken transport, or Core exit is
fatal because the surface can no longer prove its projection; input is disabled
instead of pretending local state is authoritative.

## Process ownership

The TUI owns Core as a process tree. On POSIX it launches Core in a separate
session and terminates the process group. On Windows it can use `taskkill /T /F`
while Core independently installs a kill-on-close lifetime Job Object before
async startup. Core fails closed if it cannot establish that invariant.

Each shell command has its own nested lifetime domain, described in
[Tools and changes](tools-and-changes.md). The outer Core domain handles an
abnormal Core exit; the inner command domain handles root completion, timeout,
and cancellation. These mechanisms limit orphaned processes but do not restrict
their filesystem or network access.

## Failure boundaries

| Failure | Owner response |
| --- | --- |
| malformed/oversized NDJSON | protocol error or transport close |
| incompatible protocol/product version | initialization failure; gate stays closed |
| ordinary request saturation | typed busy response |
| stdout consumer stalls | bounded write deadline then transport failure |
| stale event after Thread replacement | generation check rejects it |
| invalid typed payload | fatal surface validation error |
| request error with healthy Core | visible nonfatal result |
| Core process exits | fatal state and disabled Composer |

## Design tradeoffs

- A private process boundary adds fixture and lifecycle work but prevents Node
  presentation from importing Python execution internals.
- Strict schemas require coordinated changes in two languages; they expose drift
  before a user sees a malformed panel.
- Concurrent request dispatch improves cancellation responsiveness while the
  Application arbiter retains deterministic mutation ordering.
- Host-terminal scrollback and a natural flow avoid a second virtual terminal,
  but require explicit reconciliation for dynamic Thread state.
- A small session-only input queue improves interactivity without pretending
  that the Core executes multiple foreground actions.

## Source and test map

- Python schemas and methods: `protocol/jsonrpc.py`
- Host framing and dispatch: `protocol/stdio.py`
- Fixtures: `protocol/fixtures/v5/`, `scripts/generate_protocol_fixtures.py`
- Core process adapter: `tui/src/core/process.ts`
- Headless runner: `tui/src/cli/headless.ts`, `tui/src/cli/main.tsx`
- TypeScript schemas: `tui/src/protocol/`
- Surface reducer: `tui/src/state/`
- Input modes: `tui/src/interaction/`, `tui/src/components/Composer.tsx`
- Transcript: `tui/src/transcript/`
- Tests: `tests/unit/protocol/`, `tests/e2e/test_stdio_product.py`,
  `tui/tests/protocol/`, `tui/tests/structural/`, `tui/tests/e2e/`
