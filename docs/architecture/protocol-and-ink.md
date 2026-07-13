# stdio protocol and Ink

`tui/` is the terminal product surface. It uses Ink + React for terminal input,
layout, transcript state, pickers, theme, clipboard, and lifecycle UX. It does
not import or implement model, graph, tool, storage, memory, Skill, or MCP
behavior.

The TUI starts private `awesome-core` and exchanges JSON-RPC 2.0 requests plus
typed event notifications as newline-delimited JSON over stdin/stdout. The
protocol is versioned and bounded; malformed or oversized lines receive
protocol errors. Core logs use stderr so they cannot corrupt the event stream.

The current wire contract is Protocol v2. Protocol v1 command results and
fixtures are intentionally unsupported. Python owns serialization of the exact
`CommandOutcome` variants, TypeScript validates the generated fixtures, and
the command controller routes only their discriminators. An exhaustive
Presenter converts typed semantic payloads into terminal blocks; it has no
generic JSON or object-stringification fallback.

```text
Ink command controller
  -> Protocol v2 command.execute
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

Presentation state such as scroll position, theme, composer history, expanded
reasoning, and selection remains in the TUI. Any additional surface must adapt
the same facade/event contracts rather than becoming another execution
authority.

## Input and mode ownership

`TerminalInput.tsx` is the only Ink `useInput` subscriber. The root key router
maps keys into one discriminated UI mode: Composer, command menu, picker,
secret input, Approval, Trust, or Fatal. Exactly one mode owns Enter, Escape,
Tab, and arrow keys. Components render state and never install competing input
listeners.

The stable layout order is committed transcript, active Turn, notices or the
active interaction, Composer, then the one-line status. Welcome is committed
once into Ink static output. The Composer remains the bottom interaction area
and is removed only while another mode exclusively owns input.

## Transcript and event ordering

The TUI immediately projects a user message with a generated
`client_message_id`, then reconciles that block with the accepted Turn and the
durable transcript. Thread generation tags discard stale results after `/new`
or `/resume` without replaying them into the new transcript.

A submitted slash command is also projected immediately, using one
`command_submission_id` generated before parsing or RPC execution. It is
session-only terminal history: it never becomes a model message or SQLite
conversation entry. Command input and command result remain separate blocks so
failed, invalid, cancelled, Picker, and Secret flows preserve what the user
actually submitted.

Thread replacement is one generation-guarded Surface transition. The TUI reads
the authoritative Application and selected Thread projections, verifies both
Thread identities, hydrates durable history for resume, and dispatches one
replacement action. `/new` installs only a session notice for the new empty
Thread; `/resume` installs only the selected Thread's durable transcript. The
transition clears prior operation, interaction, warning, usage, change, and
transcript state. Events, deltas, reconciliation, or command outcomes carrying
an older generation cannot enter the replacement Surface.

Stable block identities come from their semantic owners: client message ID for
user Turns, command submission ID for slash input, durable entry ID for stored
messages, deterministic Turn segment ordinals for assistant and Thinking
segments, protocol call ID for tools, and ChangeSet ID for changes. Reusing one
key for different blocks is an invariant violation rather than a silent
deduplication.

Within an active Turn, the reducer maintains one ordered timeline of locally
measured Thinking intervals, structured tool facts, streaming assistant text,
and the measured Turn duration. Tool output remains bounded and folded by
default. Completed Markdown is parsed once; incomplete streaming Markdown uses
a stable formatter to avoid reflowing the whole transcript on each delta.

Cancellation disables input only while its RPC is unresolved. Terminal events
restore Composer mode. Approval and Auth failures remain visible and
retryable; a Core exit is fatal, renders a dedicated screen, and disables
normal input rather than pretending the operation recovered.

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
