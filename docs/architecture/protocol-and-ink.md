# stdio protocol and Ink

`tui/` is the terminal product surface. It uses Ink + React for terminal input,
layout, transcript state, pickers, theme, clipboard, and lifecycle UX. It does
not import or implement model, graph, tool, storage, memory, Skill, or MCP
behavior.

The TUI starts private `awesome-core` and exchanges JSON-RPC 2.0 requests plus
typed event notifications as newline-delimited JSON over stdin/stdout. The
protocol is versioned and bounded; malformed or oversized lines receive
protocol errors. Core logs use stderr so they cannot corrupt the event stream.

The wire contract is Protocol v3. Both initialize directions carry the strict
literal `protocol_version: 3`; a v2 peer fails the handshake explicitly even
when product versions happen to match. Python owns serialization of the exact
`CommandOutcome` variants, TypeScript validates generated fixtures, and
the command controller routes only their discriminators. An exhaustive
Presenter converts typed semantic payloads into terminal blocks; it has no
generic JSON or object-stringification fallback.

Request integers use the JSON/JavaScript interoperable safe range. Core accepts
an integral JSON number such as `3` or `3.0`, but rejects strings, booleans,
non-integral numbers, non-finite values, and integers outside
`-(2^53 - 1)..2^53 - 1`. Optional request fields are omitted when absent;
explicit `null` is accepted only where the v3 schema declares a nullable value.
The Python fixtures and strict TypeScript schemas exercise the same boundary.

The presentation path has four deliberately small layers:

```text
Core semantic facts
  -> Protocol v3 CommandPayload
  -> typed command effect for authoritative Surface state, when required
  -> exhaustive presentCommandPayload()
  -> CommandPresentation
  -> ResultPanel / AlignedRows / ResultNotice / EmptyResult
  -> Ink transcript rendering
```

Core owns facts such as credential availability, permission requirements,
Context budgets, and diagnostic states. The Presenter maps each typed payload
explicitly into user-facing rows or notices. The shared Ink components own only
terminal borders, alignment, wrapping, symbols, and semantic colors. There is
no arbitrary-record renderer, JSON formatter, or legacy presentation shape;
adding a payload or presentation variant must make the corresponding exhaustive
switch fail type checking until it is handled.

Command effects and Presenters have separate responsibilities. A semantic
effect may install authoritative product metadata, such as a persisted Thread
title, but it cannot format terminal output. The exhaustive Presenter remains
pure and converts the same payload into user-visible feedback. Commands with
no state effect pass through unchanged.

```text
Ink command controller
  -> Protocol v3 command.execute
  -> LocalApplication facade
  -> complete CommandDispatcher
  -> focused command service
  -> CommandOutcome
  -> exhaustive TUI Presenter
  -> current transcript path
```

Intent flows from Ink to the Python `ApplicationFacade`. Events flow from
Application to Ink. Request IDs, operation IDs, Thread/Turn IDs, event
sequences, and typed interaction responses let Ink reconcile live output with
durable transcript reads after reconnect or resume.

`ApplicationState.permission_mode` is the exact three-way enum Request
approval, Accept edits, or Full access. Its optional nullable
`workspace_instruction_diagnostic` is a strict `{code, source_id, message}`
record. Ink preserves that structured fact and renders its bounded message in
Welcome and the status line; `/doctor` renders each typed check detail rather
than discarding it.

Presentation state such as theme, composer history, expanded reasoning, and
selection remains in the TUI. The host terminal owns scrollback and mouse
selection. Any additional surface must adapt the same facade/event contracts
rather than becoming another execution authority.

## Bounded dispatch and process ownership

The Host has one sequential NDJSON reader, but an accepted ordinary request is
dispatched in its own task. At most 128 ordinary requests may be in flight;
additional requests receive a stable busy error, while overloaded
notifications are dropped because JSON-RPC notifications have no response.
Active and recently completed request IDs are tracked in a bounded 4,096-entry
window so duplicate work is rejected without unbounded history.

Each stdio connection starts behind a handshake gate. Before a compatible
`initialize` returns `ready`, ordinary requests receive JSON-RPC `-32002` and
never enter the Application facade. A malformed or v2 initialize does not open
the gate. While initialize is in flight, a second initialize receives the
stable `initialization_in_progress` diagnostic. A `trust_required` or
`state_reset_required` result opens only the matching startup
`interaction.respond`, another initialize, and urgent controls. Successful
workspace trust moves the connection to ready; successful state reset still
requires a later initialize that returns ready. Once ready, initialize remains
repeatable so startup retries observe the current ready snapshot.

`initialize` and `interaction.respond` use a separate bounded background
control lane. `operation.cancel` and `shutdown` are urgent controls that bypass
both request ceilings, so they remain readable while ordinary or startup work
is blocked. Only a shutdown request that passes the same JSON-RPC shape and ID
validation as normal dispatch may cancel background requests; `null`, Boolean,
floating-point, array, or object IDs receive `Invalid Request` without changing
legal in-flight work. Accepted ordinary work yields once into the Application
boundary before the next control request is read, so arrival order remains
observable. Application's foreground arbiter, not the wire dispatcher, remains
the authority that serializes Turns, direct commands, mutations, interaction
resolution, and shutdown.

All responses and events share one writer lock. The production stdout adapter
uses a 64-item worker queue and a five-second completion deadline; a full queue,
blocked consumer, or write failure closes the transport path instead of
allowing unbounded memory growth. Lines remain individually serialized, so
concurrent request completion cannot interleave JSON bytes.

The TUI owns Core as a process tree, not just as one PID. On POSIX it launches
Core in a detached session and termination signals the negative process-group
ID. On Windows it invokes the system `taskkill.exe` with `/T /F` while Core
independently installs a kill-on-close lifetime Job Object before async startup.
Core fails closed if it cannot join that job. Each Windows shell command also
gets a nested kill-on-close job: a waiting supervisor is assigned before it is
released to spawn the target. POSIX shell commands use a lease-bound session
supervisor instead. These lifetime guarantees prevent a root process or
inherited pipe from leaving cleanup unbounded; they do not sandbox Core or its
children.

## Input and mode ownership

`TerminalInput.tsx` is the only Ink `useInput` subscriber. The root key router
maps keys into one discriminated UI mode: Composer, command menu, picker,
secret input, Approval, Trust, or Fatal. Exactly one mode owns Enter, Escape,
Tab, and arrow keys. Components render state and never install competing input
listeners.

Composer text uses Ink's real terminal cursor rather than a rendered block
glyph. The cursor boundary is:

```text
Composer logical cursor
  -> TerminalFrameMetrics (React-only)
  -> InkCursorBridge (Ink 7.1 fullscreen convention)
  -> useCursor physical position
```

`useBoxMetrics()` measures the Composer's logical position inside the output,
and the existing display-width-aware viewport supplies its row and column.
`TerminalSurfaceLayout` separately publishes the measured frame height and
terminal row count. Ink 7.1 omits the trailing newline once a frame fills the
viewport, so `InkCursorBridge` translates only that renderer-specific physical
row convention. These metrics remain presentation state and never enter the
Surface store or protocol. A future Ink upgrade may remove the bridge only
after the below-, equal-, and above-viewport ANSI regression passes unchanged.

This leaves IME preedit rendering to the terminal host. Submitting and
exclusive Picker, Approval, Trust, Auth, Secret, and Fatal modes hide the
Composer cursor; the Command Menu remains a Composer accessory and keeps the
draft cursor active.

`TerminalSurfaceLayout` renders one natural terminal flow in this order:
Welcome, committed transcript, active Turn, pending inputs, notices, Command
Menu, Composer or exclusive interaction, and status. The current Thread is
dynamic React state; it is not permanent terminal output. Command Menu is a
Composer accessory, so the draft remains visible while candidates are open.
Trust, Approval, Auth, Picker, and Fatal remain exclusive interactions.

While Core owns one foreground Operation, the TUI keeps at most three pending
terminal inputs in session memory. Natural language, every Slash Command, and
`! shell` share the same FIFO. Pending input is not sent to Core, parsed, bound
to a Thread, or added to transcript history until it reaches the head. An empty
Composer uses Up to recall the newest pending input into the draft, so recall
is LIFO without changing execution order. Approval, Trust, Auth, and Picker
continue to own their keys and pause promotion.

`/new` and `/resume` execute at their ordered positions. Their authoritative
Thread transition completes before the next pending input is parsed, so that
input binds to the selected Thread. `/quit` is a terminal barrier: once queued,
later input remains in the Composer until `/quit` is recalled or executed. A
typed Operation-busy race returns the same pending identity to the queue head
without writing a failed transcript block.

## Transcript and event ordering

For immediate input, the TUI projects a user message with a generated
`client_message_id`, then reconciles that block with the accepted Turn and the
durable transcript. Thread generation tags discard stale results after `/new`
or `/resume` without replaying them into the new transcript.

Core assigns an automatic title only when it accepts the first natural-language
message. The title, first user Entry, and Turn are committed in one Application
SQLite transaction. Terminal reconciliation carries the authoritative Thread
projection together with finalized blocks; Ink never derives a second title
from displayed user text. `/rename` uses a separate typed command effect to
install the persisted manual title before showing its success notice.

A submitted slash command is also projected immediately, using one
`command_submission_id` generated before parsing or RPC execution. It is
session-only terminal history: it never becomes a model message or SQLite
conversation entry. Command input and command result remain separate blocks so
failed, invalid, cancelled, Picker, and Secret flows preserve what the user
actually submitted.

Thread replacement is one generation-guarded Surface transition:

```text
/new or /resume
  -> command.execute
  -> Application Thread service
  -> ThreadTransitionSnapshot
  -> TUI Thread transition controller
  -> one thread.replaced action
  -> CLI-owned current Ink frame reset
  -> Welcome + selected Thread redraw
```

The command result already contains the authoritative Application and selected
Thread projections. Ink verifies their identities, hydrates durable history
for resume, and dispatches one replacement action without a second state or
Thread request. `/new` installs only a session notice for the new empty Thread;
`/resume` installs only the selected Thread's durable transcript. The
transition clears prior operation, interaction, warning, usage, change, and
transcript state before the CLI host resets the current Ink frame exactly once.
It never clears terminal-global scrollback. Events, deltas, reconciliation, or
command outcomes carrying an older generation cannot enter the replacement
Surface.

Stable block identities come from their semantic owners: client message ID for
user Turns, command submission ID for slash input, durable entry ID for stored
messages, deterministic Turn segment ordinals for assistant and Thinking
segments, protocol call ID for tools, and ChangeSet ID for changes. Reusing one
key for different blocks is an invariant violation rather than a silent
deduplication.

At any instant, a terminal Turn has one display owner: live projection or
finalized transcript, never both. Reconciliation transfers the completed Turn
to finalized blocks and releases the matching live operation exactly once.

Within an active Turn, the reducer maintains one ordered timeline of locally
measured Thinking intervals, structured tool facts, streaming assistant text,
and the measured Turn duration. Each Thinking interval owns its bounded text;
there is no parallel Turn-level reasoning field. Completed intervals fold to a
locally measured duration and Ctrl+O reveals their current-session text. Raw
reasoning is never written to conversation storage.

All Tool calls between two assistant segments form one Tool Sequence even when
Thinking intervals occur between those calls. The sequence occupies exactly
one row while folded. Ctrl+O reveals each Tool's verb, target, durable outcome
and summary, local duration, safe bounded presentation detail, and an exact
omitted-entry count when Core knows it. Reconciliation matches Tools by
`call_id`: durable status and summary remain authoritative while safe live
presentation details enrich the current session. A resumed Thread deliberately
hydrates durable summaries only; it does not invent unavailable detail,
Thinking, or Worked blocks.

Worked reports the locally measured terminal Turn duration, never Provider
reasoning time, and has a dedicated status treatment. Runtime warnings with the
same stable code and normalized message are counted in one session-only block;
different messages or codes remain separate. React development warnings are
not product events, and identity collisions must still fail their invariant
tests rather than being hidden by diagnostic aggregation. Completed Markdown
is parsed once; incomplete streaming Markdown uses a stable formatter to avoid
reflowing the whole transcript on each delta.

Cancellation disables input only while its RPC is unresolved. Terminal events
restore Composer mode and allow the next pending input to advance. Approval and
Auth failures remain visible and retryable; a Core exit is fatal, renders a
dedicated screen, and disables normal input rather than pretending the
operation recovered.

Startup recovery is a typed `recovery_decision` interaction bound to its Thread,
Turn, operation meaning, and interaction generation. Verified unfinished work
offers Retry first; a potentially repeated shell or MCP side effect offers
Abort first. A claimed decision is exactly-once even if the response request is
cancelled. Core publishes a successful `interaction.resolved` before the next
`interaction.required`; both notifications have bounded delivery attempts.
When delivery remains unavailable, Core retains the same pending notification
and interaction ID for an explicit initialize retry instead of advancing the
queue or replaying the Turn.

## Request and fatal boundaries

Unexpected request exceptions are logged only to bounded Core stderr with the
RPC method, request identity, exception type, and sanitized source locations;
params and exception messages are never logged. The wire receives a generic
internal error with a stable diagnostic code. Ink treats that explicit
request-scoped diagnostic as retryable transcript feedback and reserves Fatal
for transport loss, protocol desynchronization, version incompatibility, Core
exit, or unexpected UI exceptions.

All keyboard-triggered asynchronous actions pass through one rejection guard.
It restores the owning Composer, Auth, or Approval state before showing safe
feedback. Initialization failures are represented separately from successful
startup state and render through the Fatal surface instead of being collapsed
into a one-line process error.

## Terminal color boundary

Ink components consume semantic roles rather than terminal color names.
Aurora Mist is the TrueColor brand palette; light mode uses contrast-adjusted
equivalents. Capability detection uses stdout depth and terminal signals, then
degrades explicitly through ANSI256, ANSI16, and no-color output. Status roles
remain separate from brand roles so success, warning, and failure retain their
operational meaning.
