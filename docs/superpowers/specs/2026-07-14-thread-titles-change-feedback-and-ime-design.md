# Thread Titles, Change Feedback, Thinking Defaults, and IME Design

## Status

Accepted product design. This document describes the target behavior for the
next implementation plan. It does not describe the current implementation.

## Problem Statement

Five visible behaviors currently cross the Application, conversation,
Change Journal, protocol, and Ink boundaries:

1. A tool-using Turn can show more than one Thinking block. These blocks are
   real reasoning intervals from separate model calls, but can look like an
   accidental duplicate without a clear lifecycle.
2. The transcript can render `Changed · full`, exposing an internal
   reversibility enum even when no file changed.
3. Newly created Threads default to Thinking Off, contrary to the desired
   product default.
4. Windows IME composition appears at the terminal's physical cursor rather
   than inside the visible Composer because the Composer draws a fake cursor
   and does not position Ink's real cursor.
5. Threads remain titled `New conversation`; there is no deterministic
   automatic title or explicit rename command.

The solution must correct ownership and data flow rather than add display-time
heuristics. It must not introduce another Runtime, Event Store, compatibility
layer, derived usage store, or Change statistics table.

## Product Decisions

### Thinking intervals

- Preserve every real Provider reasoning interval.
- A model-to-tool-to-model Turn may therefore contain two or more Thinking
  blocks.
- Do not merge, translate, filter, rewrite, or hide reasoning based on its
  language or similarity.
- Completed intervals remain folded by default and use the existing global
  `Ctrl+O` detail control.
- This work changes the default for new Threads, not the established
  multi-interval lifecycle.

### Change feedback

The folded transcript summary is:

```text
◇ 1 file changed · Ctrl+O to expand
```

The expanded form lists each changed item on one aligned row. Text-file rows
show additions and deletions with Git-familiar semantics:

```text
◇ 2 files changed · Ctrl+O to collapse
  src/main.py             +16  -2
  tests/test_main.py       +8  -0
```

- `+N` is green and `-N` is red in color-capable terminals.
- The `+` and `-` symbols remain in no-color terminals, so meaning never
  depends on color alone.
- Counts are line additions and deletions, not byte deltas and not net lines.
- A binary file is shown as `Bin <before> → <after> bytes`.
- A directory is shown as `Directory created` or `Directory deleted`.
- A mixed header uses natural grammar, for example
  `2 files and 1 directory changed`.
- No Change block is rendered when the structured change list is empty.
- Execute observations remain audit facts. Running a command alone is not
  presented as a file change.

### Thinking default

- Every newly created Thread starts with Thinking On.
- `/thinking off` persists Off for the current Thread.
- Resuming a Thread restores that Thread's persisted choice.
- Existing stored settings are not overwritten at startup.

### Automatic Thread titles

- A new Thread initially uses the placeholder `New conversation`.
- Its first accepted natural-language user message becomes the automatic
  title.
- Normalize consecutive whitespace to one space and trim both ends.
- Keep at most 48 terminal-visible characters. When truncation is required,
  the ellipsis occupies the final visible character: 47 visible characters
  plus `…`.
- Queued input does not name a Thread until it is promoted and accepted by
  Core.
- Once the first message is accepted, later Turn failure or cancellation does
  not revert the title because the user entry remains part of the Thread.
- Manual titles are never overwritten by later automatic naming.

### `/rename`

The only accepted form is:

```text
/rename <title>
```

- A title argument is mandatory; no Picker or follow-up input mode is opened.
- Normalize whitespace with the same rule as automatic titles.
- Reject an empty normalized title.
- Reject titles longer than 100 terminal-visible characters; do not truncate
  them.
- `/rename` is a deterministic Application command. It does not submit an
  Agent Turn or call a model.
- The command follows the existing pending-input queue rules when another
  foreground operation is active.
- Remove the hidden `/new <title>` behavior. `/new` creates a new unnamed
  Thread and accepts no title argument.

Missing arguments produce explicit feedback:

```text
Title required · /rename <title>
```

## Architectural Invariants

1. Core and Application own product facts. Ink never derives product facts
   from terminal text or the filesystem.
2. `ToolSpec.read_only` is the single authority for whether a tool can create
   a ChangeSet.
3. `/diff` and the transcript Change summary use the same Change analyzer.
4. Thread title and title provenance are persisted conversation facts.
5. The first user entry, first Turn, and automatic title update are one atomic
   conversation transaction.
6. `TerminalInput` remains the only Ink input subscriber. Composer cursor
   positioning adds no second key owner.
7. Protocol payloads are discriminated, bounded, and exhaustively presented.
   Internal enums such as `full`, `partial`, and `none` are never user-facing
   copy.
8. The new implementation replaces the old paths. No fallback formatter,
   legacy command alias, dual title source, or empty-Change compatibility path
   remains.

## Module Responsibilities

### Tool Registry and execution context

The Tool Registry continues to own tool metadata, including
`ToolSpec.read_only`. The Application tool-context factory resolves that
metadata before constructing a context:

- read-only tools receive a context without a ChangeSet;
- mutating tools acquire a ChangeSet lazily when mutation tracking is needed;
- direct `! shell` execution uses the same `execute` ToolSpec and policy path.

The TUI, protocol, and individual tools do not maintain duplicate lists of
read-only tool names.

### Change Journal and Change Analyzer

The Change Journal owns authoritative before/after observations. A focused
Change Analyzer converts those observations into two outputs:

1. the bounded unified Diff used by `/diff`;
2. structured presentation facts used by the transcript summary.

The analyzer is pure over recorded Change data. It does not shell out to Git
and therefore works in non-Git workspaces. Its line-count semantics match the
addition/deletion meaning familiar from Git Diff, while directory records
remain Awesome-specific because Git does not track empty directories.

Derived counts are computed on read and are not persisted in a second table.
This keeps the journal as the single stored authority and prevents Diff and
summary statistics from drifting.

### Conversation service and storage

Conversation owns:

- Thread title;
- title provenance: `automatic` or `manual`;
- Thinking preference;
- first-entry and Turn creation.

The storage contract must support an atomic operation that creates the first
user entry and Turn while applying the automatic title when the current title
is still eligible for automatic naming. A manual rename is an explicit update
of both title and provenance.

The repository retains one current schema and one current model. It does not
add a legacy title inference path. Development data from an incompatible
discarded schema is recreated rather than translated by a permanent adapter.

### Application commands

The conversation command service adds `rename` and validates command
arguments. It calls the Conversation service and returns a typed
`ThreadRenamedPayload` containing the authoritative Thread identity, title,
and provenance.

`/new` rejects arguments. There is no deprecated alias and no hidden prompt
submission path.

### Protocol

The protocol transports structured Change and rename facts. It does not carry
rendered terminal strings.

Change items form an explicit discriminated union:

```text
TextFileChange
  path
  change_kind
  additions
  deletions

BinaryFileChange
  path
  change_kind
  before_bytes
  after_bytes

DirectoryChange
  path
  change_kind
```

The Python producer and TypeScript consumer must agree on nullability,
enumerations, bounds, and field names through deterministic cross-language
fixtures.

### Surface projection and Presenters

The Surface stores the authoritative current Thread projection received from
Core. Turn reconciliation refreshes both transcript blocks and Thread metadata
from `thread.read`; it must not update messages while leaving a stale title.

The command Presenter handles `ThreadRenamedPayload` and emits one clear
success result. Change projection renders only non-empty structured changes.
It never parses `/diff`, rereads workspace files, or renders reversibility as a
status label.

### Composer and terminal cursor

The Composer uses Ink's real cursor support and layout metrics:

- calculate the cursor column from terminal display width, not JavaScript
  string length;
- account for CJK characters, emoji, wrapping, Composer borders, and terminal
  resize;
- activate the cursor only while Composer owns input;
- hide it while Trust, Approval, Secret Input, Picker, or Fatal owns input, and
  while the Composer is unavailable;
- remove the fake block cursor once the real cursor is authoritative.

There are no Windows-specific key routes or IME protocol emulations. The real
cursor gives the host terminal and IME the correct composition anchor.

## Data Flows

### Tool and Change flow

```text
Tool request
  -> Tool Registry resolves ToolSpec.read_only
  -> Application constructs ToolExecutionContext
  -> mutating operation lazily acquires ChangeSet
  -> Change Journal records before/after facts
  -> Change Analyzer derives Diff and structured change items
  -> Application event / Thread projection
  -> typed protocol payload
  -> Surface Change block
  -> folded or expanded Ink presentation
```

Read-only tools stop before ChangeSet acquisition. An execute-only journal
observation with no recorded filesystem item produces no Change block.

### Automatic title flow

```text
/new
  -> create Thread with automatic provenance and placeholder title
  -> first natural-language input reaches queue head
  -> Core accepts submission
  -> normalize and bound candidate title
  -> atomic title + user entry + Turn transaction
  -> operation events
  -> thread.read reconciliation
  -> Surface replaces Thread metadata and transcript projection
```

### Manual rename flow

```text
/rename <title>
  -> command parser
  -> ConversationCommandService validation
  -> ConversationService rename
  -> Thread storage update with manual provenance
  -> ThreadRenamedPayload
  -> command Presenter success result
  -> Surface updates current Thread metadata
```

### IME flow

```text
Composer draft + layout metrics
  -> terminal-display-width cursor calculation
  -> Ink real cursor position
  -> host terminal IME composition anchor
```

IME preedit text remains terminal-owned until committed. Awesome owns the
draft only after Ink receives committed input.

## Failure Semantics

### Rename failures

- missing or empty title: `invalid_arguments` with
  `Title required · /rename <title>`;
- title longer than 100 visible characters: `invalid_arguments` with the
  maximum stated;
- no current Thread: `thread_not_found`;
- storage failure: the Thread title remains unchanged and no success result is
  emitted;
- operation busy race: retain the same pending input identity at the queue
  head under the existing queue contract.

### Automatic title failures

Title update, first user entry, and Turn creation succeed or fail together.
No Turn may exist with only part of that transaction committed. A later model,
tool, cancellation, or Provider failure does not undo the already committed
title and user entry.

### Change analysis failures

- undecodable content is represented as a binary change rather than receiving
  fabricated line counts;
- a missing authoritative blob or impossible journal reference is a Core
  invariant failure and is surfaced through existing sanitized error
  semantics;
- Ink does not catch an invalid payload and replace it with guessed display
  text;
- an empty structured change list produces no Change block, not an empty
  success banner.

### Cursor failures

Cursor positioning is a presentation concern. Layout changes recalculate the
position. Losing Composer ownership hides the cursor. The TUI does not alter
the draft or reset the Application operation when the terminal cannot expose
color or cursor capabilities.

## Validation Strategy

### Change behavior

- every read-only built-in Tool avoids ChangeSet allocation;
- text create, edit, and delete produce exact addition/deletion counts;
- created and deleted files count all added or removed lines respectively;
- binary files and directories use their dedicated variants;
- mixed summaries use correct singular/plural grammar;
- execute-only observations do not display `Changed`;
- `/diff` and Change summaries share analyzer fixtures;
- Change is folded by default and `Ctrl+O` expands and collapses it;
- green additions and red deletions degrade to visible `+` and `-` symbols in
  no-color output;
- `full`, `partial`, and `none` cannot enter terminal presentation fixtures.

### Thread titles and rename

- first accepted natural-language input sets an automatic title atomically;
- queued input does not name a Thread before promotion;
- whitespace normalization and 48-visible-character truncation are
  deterministic for ASCII, CJK, and emoji;
- cancellation or failure after Turn creation retains the title;
- manual rename persists and prevents later automatic overwrite;
- manual titles accept at most 100 visible characters and reject longer input;
- missing and empty `/rename` arguments return explicit feedback;
- `/new` arguments are rejected and no hidden title path remains;
- `/new`, `/resume`, Thread listing, status projection, and process restart
  show the same authoritative title;
- protocol producer fixtures validate in the TypeScript consumer.

### Thinking

- a new Thread defaults to Thinking On;
- `/thinking off` persists for that Thread and survives resume;
- creating another Thread returns to the On default;
- multiple real Thinking intervals remain independently ordered, folded, and
  expandable.

### IME and cursor

- cursor coordinates account for borders, wrapping, resize, CJK, and emoji;
- Composer is the sole cursor owner in Composer mode;
- exclusive interactions hide the Composer cursor;
- automated tests verify cursor calculations and input-mode ownership;
- one manual Windows PowerShell acceptance run verifies Pinyin preedit appears
  at the visible Composer position and committed text enters the draft once.

### Regression boundary

Run formatting, lint, Python and TypeScript type checks, focused unit tests,
cross-language protocol fixtures, affected Application/Storage integration
tests, and the focused Ink product flow. Live-provider, installer, full E2E,
and cross-platform release suites are deferred unless an affected boundary
requires them.

## Documentation Impact

After implementation, update the current-behavior documentation:

- command reference for `/rename`, `/new`, and default Thinking behavior;
- protocol and Ink architecture for real cursor ownership and structured
  Change presentation;
- Application and LangGraph architecture for atomic automatic naming;
- storage architecture for title provenance;
- English and Chinese user-facing documents together where behavior is
  described.

This design document remains the planning record. Authoritative architecture
documents must be updated only when the behavior is implemented.

## Explicit Non-Goals

- summarizing or translating model reasoning;
- merging separate reasoning intervals;
- AI-generated Thread titles or an extra model call;
- an interactive `/rename` modal;
- title history or aliases;
- persisted derived Change statistics;
- parsing Change facts in the TUI;
- Git as a required Change backend;
- a custom Windows IME implementation;
- compatibility adapters for discarded command, title, or Change shapes.
