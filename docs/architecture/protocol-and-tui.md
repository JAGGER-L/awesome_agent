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
Frame enforcement is currently asymmetric, as described below.

## Protocol v3 contract

Initialization requires literal `protocol_version: 3`, client name `awesome`,
and the same product version as Core. A v2 client fails explicitly even if its
product version matches. Protocol and product versions answer different
questions: wire compatibility versus release identity.

Request IDs are JSON/JavaScript-safe integers or strings as allowed by the
JSON-RPC parser. Numeric IDs must be integral, finite, non-Boolean, and within
`-(2^53 - 1)..2^53 - 1`. Optional fields are omitted when absent; explicit
`null` is accepted only by a schema that declares it nullable.

The current request methods are:

| Method | Purpose |
| --- | --- |
| `initialize` | negotiate identity and perform startup/bootstrap |
| `application.getState` | read authoritative Application state |
| `thread.list` | page workspace Threads |
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

## Cross-language evidence

Python owns serialization of method results, `CommandOutcome` variants, and
events. `scripts/generate_protocol_fixtures.py` writes deterministic valid and
invalid fixtures under `protocol/fixtures/v3/`. TypeScript Zod schemas validate
the same corpus.

```text
Python Pydantic contracts
  -> generated v3 fixtures + manifest hashes
  -> TypeScript strict Zod schemas
  -> protocol contract tests
  -> exhaustive reducers/presenters
```

There is no generic JSON fallback. Adding a payload variant must update the
Python type, fixture generator, TypeScript schema, reducer/effect when needed,
Presenter, and tests. TypeScript exhaustive switches then expose missing cases
at compile time.

## Handshake state machine

The stdio Host gates Application access:

```text
UNINITIALIZED
  -> initialize in flight: INITIALIZING
  -> ready result: READY
  -> trust_required/state_reset_required: BOOTSTRAP_INTERACTION
  -> failure: previous state

BOOTSTRAP_INTERACTION
  -> matching interaction.respond
  -> trust accepted: READY
  -> reset accepted: initialize again
```

Before `READY`, ordinary requests receive a stable server-not-initialized or
server-not-ready error. A second concurrent initialize receives
`initialization_in_progress`. During bootstrap, only the matching interaction,
another initialize, cancellation, and shutdown are admitted. A malformed or v2
initialize never opens the gate.

Initialization remains repeatable after `READY` so a surface retry can observe
the current snapshot without creating a second Application.

## Framing and dispatch bounds

Core limits each request line to 1 MiB. The TUI limits both encoded requests and
decoded Core frames to the same size. Core's output writer serializes one whole
line but does not currently reject an output frame larger than 1 MiB. Core reads
one sequential byte stream, while accepted ordinary requests run in independent
tasks. This prevents a slow provider or command from blocking urgent control
parsing.

| Resource | Bound |
| --- | ---: |
| Core request line | 1 MiB |
| TUI encoded/decoded frame | 1 MiB |
| Core output frame | no explicit size preflight |
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

The asymmetric frame bound is observable: a schema-valid `thread.read` page can
contain as many as 500 large entries, and `/tools` has no aggregate pagination.
Either response can exceed 1 MiB, be written by Core, and then be treated as a
fatal oversized frame by the shipped TUI. Callers should use smaller transcript
pages; `/tools` currently has no caller-side mitigation. A runtime fix needs an
explicit Core output policy—reject, paginate, or chunk—plus cross-language
regression fixtures rather than a documentation-only promise.

Wire concurrency is not mutation concurrency. The Application foreground
arbiter still decides which Turn, direct command, state mutation, interaction,
or shutdown may run.

## Semantic presentation pipeline

Core sends facts, not preformatted terminal widgets:

```text
Application fact
  -> Protocol v3 payload
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
  -> React TerminalFrameMetrics
  -> InkCursorBridge
  -> useCursor physical terminal position
```

The bridge isolates an Ink 7.1 fullscreen convention where a frame filling the
viewport omits a trailing newline. Terminal frame metrics remain local
presentation state. A future Ink upgrade can remove the bridge only after
below-, equal-, and above-viewport ANSI regressions still pass.

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
- `/new` and `/resume` change which Thread receives the following item;
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
- Fixtures: `protocol/fixtures/v3/`, `scripts/generate_protocol_fixtures.py`
- Core process adapter: `tui/src/core/process.ts`
- TypeScript schemas: `tui/src/protocol/`
- Surface reducer: `tui/src/state/`
- Input modes: `tui/src/interaction/`, `tui/src/components/Composer.tsx`
- Transcript: `tui/src/transcript/`
- Tests: `tests/unit/protocol/`, `tests/e2e/test_stdio_product.py`,
  `tui/tests/protocol/`, `tui/tests/structural/`, `tui/tests/e2e/`
