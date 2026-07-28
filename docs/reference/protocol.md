# Private Core/TUI protocol v5

Awesome's Ink process and its one Python Core child communicate over private
stdio using newline-delimited JSON-RPC 2.0. The protocol is an internal
component boundary, not a remote API: it has no network listener, authentication
scheme, compatibility proxy, or promise that third-party clients can mix
versions independently.

Protocol version **5** is paired with the exact installed product version. The
current repository product version is **1.3.0**. Event envelopes have their own
version **1**. Both contract identifiers come from `contract-versions.json`;
the product value continues to come from `VERSION`.

## Process and transport

- Ink launches one `awesome-core` child in the active workspace.
- Requests and Core events use UTF-8 NDJSON on stdin/stdout: exactly one JSON
  object per line.
- Core logs go to stderr; stdout is reserved for protocol frames.
- Core's request reader accepts at most 1,048,576 bytes before the newline.
  Empty lines are ignored; invalid UTF-8, invalid JSON, or an oversized request
  returns JSON-RPC parse error `-32700` and the reader continues.
- The TUI applies the same 1 MiB limit when encoding requests and decoding Core
  output. Core measures compact UTF-8 content before writing stdout. An
  oversized request result is replaced with the bounded, non-retryable product
  error `result_too_large`; an oversized event is rejected before any bytes are
  written. Core never emits a frame that the bundled TUI must reject for size.
- Output serialization is compact UTF-8 JSON with non-ASCII text preserved.
  Non-finite numbers and text that cannot be represented as UTF-8 are never
  written. An otherwise completed request with an unrepresentable result is
  replaced by one redacted `internal_error` Application failure using the same
  request ID.
- The bounded stdout queue holds 64 frames; an undrained/full queue or a write
  that does not complete within five seconds breaks the channel rather than
  allowing unbounded memory growth.
- EOF or a fatal channel error shuts down the Application if an explicit
  shutdown did not already do so.

```text
Ink                     Protocol Host              Application
 |                            |                          |
 |-- initialize(v5, exact) -->|                          |
 |                            |------- initialize ------>|
 |                            |<-- ApplicationResult ----|
 |<---- JSON-RPC response ----|                          |
 |                            |                          |
 |------ turn.submit -------->|                          |
 |                            |-- atomically admit ----->|
 |<---- event: operation.started ------------------------|
 |                            |<-- OperationAccepted ----|
 |<---- JSON-RPC response ----|                          |
 |<---- event: turn.started / deltas / tools ------------|
 |<---- event: turn.completed ---------------------------|
 |<---- event: operation.completed ----------------------|
```

`operation.started` is written before the Application returns
`OperationAccepted`, so it precedes the matching JSON-RPC response on the
shared output stream. Once the Operation task is scheduled, later Turn or Tool
events can also race that response. The bundled TUI starts its event consumer
before submitting work and correlates these self-identifying events by
`operation_id`, `turn_id`, and `client_message_id`; a client must not discard
an event merely because its request promise has not resolved yet.

## JSON-RPC request shape

```json
{"jsonrpc":"2.0","id":"request-1","method":"application.getState","params":{}}
```

Only `jsonrpc`, `id`, `method`, and `params` are allowed. `jsonrpc` must be
`"2.0"`; `method` must be a non-empty string; `params` defaults to `{}` and must
be an object for a registered method. An ID is a 1–128 Unicode-scalar string
without unpaired UTF-16 surrogates, or a JavaScript-interoperable safe integer;
it is never a boolean or `null`. Core and TUI enforce the same contract. Typed
integer fields in method parameters use the same safe range and must be
integral JSON numbers.

The safe-integer rule also covers every integer-valued number in results,
errors, and events, including sequence numbers, token counters, durations, and
generic diagnostic data. Non-integral numbers must be finite. Before writing a
frame, Core recursively rejects unsafe integral numbers, non-string object
keys, invalid Unicode, non-JSON containers, and structures deeper than 64
levels; the TUI applies the corresponding safe-number rule when decoding.

Notifications omit `id` and receive no response, even on method/parameter
failure. Production clients should use requests for lifecycle/control methods
so they can observe acceptance. IDs cannot be reused while active or among the
4,096 most recently completed IDs; a duplicate is `-32600 Invalid Request`.

Wire models are strict. Unknown properties fail validation. Optional values are
normally omitted instead of sent as `null`; list/read pagination fields and the
credential `api_key` explicitly reject null.

## Handshake state machine

Application's `ApplicationBootstrap` begins in `UNINITIALIZED`. The Host has no
independent bootstrap phase: it queries Application admission and translates a
rejection to the existing handshake error. `initialize` must use:

```json
{
  "jsonrpc": "2.0",
  "id": "init-1",
  "method": "initialize",
  "params": {
    "protocol_version": 5,
    "client_name": "awesome",
    "client_version": "1.3.0"
  }
}
```

Protocol mismatch returns product error `protocol_version_incompatible`.
`client_name` other than `awesome` or a product version unequal to Core returns
`client_version_incompatible`. A v3 client therefore fails explicitly even when
its package version happens to equal Core's.

A successful `InitializeResult` contains product/protocol versions, session ID,
workspace presentation, capabilities, and one status:

| Status | Meaning | Next action |
| --- | --- | --- |
| `ready` | Workspace and state are active. | Normal methods are admitted. |
| `trust_required` | Repository-controlled input has not been trusted. | Resolve the supplied interaction; `trust` advances Application to ready. |
| `state_reset_required` | Application state is older than schema 7. | Resolve reset/deny; after reset, initialize again. |

Current capabilities are `threads`, `turns`, `direct_commands`, `commands`,
`tools`, `skills`, `mcp`, `local_memory`, `mem0_cloud`, `web`, and `citations`.

The Host never parses a serialized result payload to advance readiness; typed
initialize and interaction outcomes update Application first. This ownership
is internal and remains part of the Protocol v5 wire contract.

Before ready, ordinary requests receive JSON-RPC `-32002` with diagnostic
`server_not_initialized` or `server_not_ready`. The deliberate exception is
`skill.list`, `skill.install`, and `skill.remove`: each is admitted only while
Application is exactly `UNINITIALIZED`. Application reserves one mutually
exclusive pre-initialize transition for the request. While it is active,
another Skill package request or `initialize` receives
`preinitialize_operation_in_progress`. Completion does not call `initialize`
or advance the bootstrap phase; Application remains `UNINITIALIZED`.

Once initialization has started or Application has entered any later phase,
all three Skill package methods receive
`skill_management_requires_uninitialized`. A private client may therefore run
package methods sequentially and then initialize that same Core; initialization
discovers the resulting User packages. This does not hot-update the immutable
catalog of an already initialized Session. While initialize is running, a
second initialize receives `initialization_in_progress`. A matching bootstrap
`interaction.respond` is admitted while waiting. `operation.cancel` and
`shutdown` are urgent controls and remain admitted before ready.

## Method catalog

All method results use the `ApplicationResult<T>` envelope described below.
Lengths are Unicode string lengths after JSON decoding.

| Method | Strict params | Successful value |
| --- | --- | --- |
| `initialize` | `protocol_version`; `client_name` 1–128; `client_version` 1–64 | `InitializeResult` |
| `skill.list` | `{}` | `{ "skills": [{ "name": string, "description": string }] }`; at most 512 entries, unique and name-sorted |
| `skill.install` | `source_path` 1–4,096 with no surrounding whitespace, NUL, CR, or LF; optional strict Boolean `replace`, default false | `{ "name": string, "status": "installed" | "replaced" }`; name is canonical |
| `skill.remove` | canonical `name` matching `[a-z][a-z0-9-]{0,63}` | `{ "name": string, "status": "removed" }` |
| `application.getState` | `{}` | Current `ApplicationState` snapshot |
| `thread.list` | optional `cursor` 1–1,024; `limit` 1–200, default 50 | Threads, `has_more`, optional next cursor |
| `thread.search` | trimmed `query` 1–200; optional `cursor` 1–1,024; `limit` 1–50, default 50 | The same `ThreadListResult` as `thread.list` |
| `thread.read` | `thread_id` 1–128; optional `before_sequence >= 1`; `limit` 1–500, default 100 | Thread view, ChangeSets, reverse-pagination marker |
| `turn.submit` | `thread_id`; `content` 1–200,000; `client_message_id` matching `client_[A-Za-z0-9_-]+`, max 128 | Operation, Thread, Turn, and client-message IDs |
| `direct.execute` | `thread_id`; `command` 1–8,000, matching the delegated `execute` tool | Operation and Thread IDs |
| `command.execute` | `name`; optional `arguments` string array | One typed `CommandOutcome` |
| `provider.credential.set` | see below | Provider, status, optional selected source, diagnostic code |
| `interaction.respond` | `interaction_id` 1–128; `decision` enum | Accepted flag and status |
| `operation.cancel` | `operation_id` 1–128 | Operation ID and whether cancellation was requested |
| `shutdown` | `{}` | `{ "stopped": true }` |

The three `skill.*` methods are private package-management support for the
one-shot `awesome skills` CLI, not Agent tools. They neither create a Thread or
Turn nor construct a Workspace Runtime. Their phase and concurrency rules are
part of bootstrap admission, not a second Host-owned state machine.

`direct.execute` validates the same 8,000-character command boundary as the
delegated `execute` tool before reserving an Operation. Oversized commands are
rejected synchronously as invalid params and never start a process.

### `application.getState`

The snapshot includes initialization/session/workspace identity and trust,
selected Thread, the model catalog and model identity, Thinking/Skill/permission
modes, active operation and pending interaction IDs, configuration
validity/diagnostics, secret presence and credential source status, Memory/MCP
summaries, usage, and the structured workspace-instruction diagnostic. Secret
values are never part of state.

`model_catalog` is the Protocol v5 projection of the static, provider-neutral
`ModelCatalog -> ProviderDescriptor -> ModelProfile` directory. A Provider
descriptor carries `id`, `credential_id`, `supported_regions`, optional
`default_region`, and its model profiles. A model profile carries `id`,
`context_limit`, `supports_tools`, `supports_reasoning`, and `is_default`.
Provider and model IDs are unique, every model belongs to its Provider prefix,
and each Provider has exactly one catalog default. The current value contains
DeepSeek and Kimi with four models; Tavily Web Provider selection data remains
in the separate Web/configuration boundary and never appears here.

The static catalog is distinct from `provider_credentials` and
`model_identity`: credential presence/source, configured default selection,
active Thread selection, and Kimi region selection remain dynamic
Application/configuration facts. Every model Provider's `credential_id` has a
matching credential status, and all IDs in `model_identity` must resolve in the
catalog.

The TUI validates this snapshot and derives startup and provider setup from
`model_catalog` plus `provider_credentials`; it has no copied model/Provider
enumeration. `/model` choices are a `CommandSelection` produced by Application
from the same catalog and rendered generically by the TUI.

`workspace_instruction_diagnostic` is independent of `configuration_valid`.
For example, an oversized `AGENTS.md` can be ignored and warned about without
making otherwise valid YAML configuration unusable.

### Pagination

`thread.list` uses an opaque cursor; clients must not decode or synthesize it.
`thread.search` trims its query, searches only the active Workspace, and orders
matches by `updated_at DESC, id DESC`. Its opaque cursor hash-binds that
Workspace and normalized query, so replaying it for another scope is invalid
while the cursor carries no cleartext workspace key. Search is an
ASCII-case-insensitive literal substring operation over Thread titles and all
durable transcript entry content. It excludes ToolActivity, summaries,
checkpoints, and metadata, and provides no FTS, tokenization, snippets,
relevance ranking, or complete Unicode case folding. Every page query has a
5,000,000 SQLite VM-op scan budget. Exhaustion returns the existing
`result_too_large` Product error; clients should refine the query. Unlike the
50-result `/search` picker, the RPC remains keyset-paginated through
`has_more` and `next_cursor`.
`thread.read` paginates backward with `before_sequence` and returns
`next_before_sequence` when more entries exist. Explicit null pagination fields
are invalid so “not supplied” has one unambiguous wire representation. The
Application dynamically reduces a requested page until the encoded result fits
its 900 KiB budget and preserves `next_before_sequence` for the omitted entries.

Assistant entry metadata contains an ordered `citations` array. Each strict
source has `id` (`S1...`), bounded single-line `title`, and an absolute HTTPS
`url`; IDs are contiguous and URLs unique within the Turn. Turn budgets and
usage also include non-negative `web_requests`, whose configured hard maximum
is eight.

### Turn and direct acceptance

`turn.submit` and `direct.execute` acknowledge admission, not completion. The
returned `operation_id` correlates later events. `client_message_id` makes a
Turn submission idempotent at the conversation boundary; clients create a new
one for a new user intent and retain it across uncertainty about the response.

Core acquires the foreground lease before persisting the Turn, so
`operation_busy` does not leave a phantom in-progress Turn. Direct commands use
the same operation, schema, shell hard-deny, process, Change Journal, and audit
boundaries as Agent `execute` calls. They deliberately do not use the Thread's
ordinary approval matrix: the exact `!` input is explicit user authority and the
Direct Operation gets an independent Full-access permission session.

### `command.execute`

Params are a closed `CommandIntent`:

```json
{"name":"mcp","arguments":["status","repository-index"]}
```

Only the 26 Application-owned names are normally sent over this method. Ink
owns `help`, `theme`, `copy`, and `quit` locally. A `CommandOutcome` contains
exactly one branch: typed `result`, typed `interaction`, or stable command
`error`. Exact grammar and foreground snapshot exceptions are in
[Slash Commands](commands.md).

Every Protocol v5 Thread projection has a required nullable `lineage` field.
It is `null` for a root Thread, or a strict object with `kind` (`fork` or
`retry`), `source_thread_id`, and `source_turn_id` for one immediate parent.
This field records provenance only; clients must not infer a shared transcript
DAG or fetch history through it.

`thread_transition` carries one authoritative Application/Thread snapshot. Its
`reason` is `new`, `resume`, or `fork`: `new` requires null lineage, `fork`
requires fork lineage, and `resume` may select a root or materialized Thread. A
plain transition with reason `retry` is invalid. Retry instead returns the
strict combined `thread_retry` payload: a transition whose reason and lineage
are both retry, plus an `operation` containing non-null `operation_id`,
`thread_id`, `turn_id`, and `client_message_id`. The transition Thread must be
the Operation Thread, and the Operation Turn must already exist in that
transition. This atomic result prevents a surface from installing a new Thread
without also knowing the foreground Operation it owns.

The `thread_export` command result contains only `kind`, `thread_id`, `path`
(1–1,000),
`format` (`markdown` or `json`), `write_status` (`created`, `updated`, or
`unchanged`), `byte_count`, and an optional `change_set_id`. Created and updated
exports require a ChangeSet ID; unchanged exports forbid one. This payload does
not carry exported content, workspace identity, or internal transcript
metadata. Export output itself is capped at 5 MiB; the path bound is checked
after normalization and before mutation. A failed attempt without reconciled
file evidence emits no empty ChangeSet result.

The `/tools` result is not paginated. Catalog admission applies its own
aggregate bounds; the transport's final byte check still returns
`result_too_large` instead of emitting an invalid frame if another producer
breaks that invariant.

### `provider.credential.set`

`provider` is `deepseek`, `kimi`, `mem0`, or `tavily`; `action` is `add`, `replace`, or
`delete`; `allow_unverified` defaults to false. Add/replace require a non-empty
`api_key` of at most 20,000 characters without CR/LF. Delete forbids both key
content and `allow_unverified: true`.

This is a mutation/external operation under the foreground arbiter. The key is
wrapped as a secret immediately and never copied into events, errors, or state.
For DeepSeek and Kimi, Core performs remote validation; an unreachable Provider
may return a confirmation path for an explicit save-unverified retry, while a
key the Provider rejects is not saved. For `mem0` and `tavily`, Core performs no
remote credential validation and saves any locally valid input; an invalid key
fails only when a later Mem0 or Web operation reaches the service.

The result contains `source` only when Core can report a selected credential
source. It is normally `awesome` after a successful save, but invalid,
save-unverified-confirmation, or delete results can have no selected source; in
that case the field is omitted. Explicit `"source": null` is not a valid v5
result.

### `interaction.respond`

Decision values are `trust`, `reset_state`, `allow_once`,
`allow_thread_writes`, `allow_thread_network`, `enable_full_access`, `retry`,
`abort`, and `deny`. The
interaction kind and advertised choices determine which values are valid.
Core revalidates the pending interaction's generation and its required
Thread/Turn/operation/permission binding. Stale responses do not mutate current
authority.

### `operation.cancel` and `shutdown`

Cancel is best-effort and identity-specific. A true result means cancellation
was delivered while the matching Operation was still cancellable, and its
terminal outcome is `operation.cancelled`. False also covers an unknown ID, an
Operation already cancelling, or an Operation in `committing` or a terminal
phase. Cancellation after the commit boundary cannot rewrite the durable
outcome. An already-admitted durable write waits for a known COMMIT or ROLLBACK
before the first caller cancellation is re-raised; shutdown waits for the same
boundary. The original completed/failed terminal event is published instead.
Local activity, transcript, ChangeSet, and checkpoint finalization retains the
Operation lease until its result is known. Process/MCP cleanup and best-effort
terminal publication remain bounded.

Shutdown is urgent. A valid request first cancels other background requests,
prevents new foreground leases, cancels/waits for active operations and
mutations, closes MCP and other resources, then returns. A valid shutdown
notification terminates without a response.

## Application result envelope

Transport success does not imply product success. Every registered method
returns exactly one branch:

```json
{"ok":true,"value":{"stopped":true}}
```

or:

```json
{
  "ok": false,
  "error": {
    "code": "operation_busy",
    "message": "Another foreground operation is active.",
    "retryable": true,
    "data": {}
  }
}
```

`value` and `error` are mutually exclusive. Error messages are bounded to 2,000
characters and `data` contains only safe structured diagnostics.

Product error codes are:

| Area | Codes |
| --- | --- |
| Configuration/workspace | `configuration_invalid`, `workspace_not_trusted`, `model_not_configured`, `provider_not_configured` |
| Conversation/foreground | `thread_not_found`, `turn_not_found`, `turn_busy`, `operation_busy`, `recovery_required` |
| Input/commands | `invalid_arguments`, `command_not_available` |
| Output bounds | `result_too_large` |
| Checkpoints | `checkpoint_missing`, `checkpoint_corrupt` |
| Compatibility/state | `client_version_incompatible`, `protocol_version_incompatible`, `state_created_by_newer_version`, `state_unknown`, `state_unavailable`, `state_reset_busy`, `state_reset_failed` |
| Invariant failure | `internal_error` |

Clients use the `retryable` flag plus current state, not string matching on the
message, to decide whether a retry is appropriate.

In Protocol v5, an Application-level `state_unavailable` error is retryable and
includes bounded `state_directory` metadata. This is distinct from the
non-retryable `state_unavailable` inside a built-in Memory tool's `ToolOutput`;
the two envelopes must not be normalized by code string alone.

## JSON-RPC errors

Failures before or outside the Application contract use JSON-RPC errors:

| Code | Meaning |
| ---: | --- |
| `-32700` | Invalid JSON/UTF-8 or line too large (`Parse error`) |
| `-32600` | Invalid request shape or duplicate recent ID |
| `-32601` | Unknown method |
| `-32602` | Params fail the strict method schema |
| `-32603` | Unexpected handler failure; data contains only `diagnostic_code: core_request_failed` |
| `-32002` | Handshake state does not admit this method |
| `-32000` | Protocol in-flight capacity is exhausted (`Server busy`) |

An unexpected request exception is logged internally with method, request ID,
exception type, and a compact stack location, while the client receives the
fixed `-32603` diagnostic. This avoids leaking raw arguments, schemas, paths, or
credentials.

## Events

Events are JSON-RPC notifications:

```json
{
  "jsonrpc": "2.0",
  "method": "event",
  "params": {
    "version": 1,
    "event_id": "event_001",
    "sequence": 1,
    "session_id": "session_...",
    "workspace_key": "ws_...",
    "operation_id": "operation_...",
    "event_type": "operation.started",
    "timestamp": "2026-07-11T08:00:00Z",
    "payload": {"kind":"operation.started","message":""}
  }
}
```

Every envelope has version, unique event ID, monotonically increasing
session-local sequence, session/workspace identity, UTC timestamp, event type,
and a discriminated payload whose `kind` equals `event_type`. Thread, Turn,
operation, and client-message IDs appear when relevant. Operation lifecycle
events require an operation ID; Turn lifecycle events require Thread and Turn
IDs.

| Family | Event types | Key payload fields |
| --- | --- | --- |
| Operation | `operation.started`, `.completed`, `.failed`, `.cancelled` | bounded message |
| Turn | `turn.started`, `.completed`, `.failed`, `.cancelled` | optional reason; terminal duration |
| Assistant stream | `assistant.text.delta`, `assistant.reasoning.delta` | non-empty text, max 30,000 per event |
| Provider retry | `provider.retrying` | attempt 2–7, maximum 1–7, delay 0–30 seconds, error code |
| Tool | `tool.started`, `.completed`, `.failed`, `.cancelled` | call/name/verb/target; terminal outcome, summary/detail, duration, optional error code |
| Context | `context.prepared`, `context.compressed` | source count and estimated tokens |
| Usage | `usage.updated` | non-negative input/output/reasoning/cache token and Web-request counters |
| Memory | `memory.status` | `local` or `external`, enabled flag, status |
| Interaction | `interaction.required`, `interaction.resolved` | bound IDs/kind/prompt/operation/target/capability/choices or decision |
| Warning | `warning` | stable code and bounded message |

The emitter enforces one start and at most one terminal lifecycle event for a
given operation or Turn. The Tool Executor likewise finalizes one ToolActivity
and one terminal tool event per call. Consumers should render by sequence and
correlation IDs rather than assuming response/event arrival order across
concurrent requests. In particular, `operation.started` for an accepted Turn or
Direct command precedes its acceptance response, and subsequent events may
arrive before that response is handled.

The same ordering applies to `thread_retry`, but its Thread identity does not
exist on the surface until the combined command response is installed. A v5
surface therefore opens a local retry gate before issuing the command, buffers
events in sequence, installs the returned transition, binds the new generation
to the returned Operation/Thread/Turn identities, and then replays the buffer.
The gate accepts at most 1,024 events and 4 MiB of encoded content. Capacity or
identity violations are protocol desynchronization and must fail closed; an
event must never be rendered on the source Thread merely because it arrived
first.

## Concurrency and backpressure

The Host admits at most 128 regular in-flight requests and 16 background control
requests. `initialize` and `interaction.respond` use the control pool;
`operation.cancel` and `shutdown` are handled urgently. After scheduling an
accepted request, the reader yields once so it can enter the Application
boundary before a following control request.

This transport concurrency does not permit concurrent product mutations. The
Application Foreground Arbiter atomically coordinates Turns, direct commands,
state-changing/external commands, credential mutation, non-tool interaction
resolution, and shutdown. Snapshot commands may run during an operation only
where the command contract explicitly allows them.

## Fixtures and compatibility testing

`protocol/fixtures/v5/` is the cross-language source of truth. It contains valid
and invalid methods, command results, events, product failures, plus a manifest
with file hashes, method names, event names, product version, and protocol
version. Python Pydantic models and the TUI's strict TypeScript/Zod schemas both
validate these fixtures.

When changing a wire contract:

1. change Core and TUI schemas together;
2. update valid and negative fixtures plus manifest hashes;
3. preserve fixed/redacted diagnostics and strict unknown-field rejection;
4. bump protocol version for an incompatible shape or semantic change;
5. verify that the previous protocol version fails its handshake clearly.

Do not add a compatibility adapter merely to let mismatched private components
continue. The launcher ships them as one versioned unit, so fail-fast detection
is safer than ambiguous partial compatibility.
