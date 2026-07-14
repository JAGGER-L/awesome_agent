# Fullscreen Cursor and State Schema Diagnostics Design

## Status

Accepted direction: keep Awesome's current inline Ink interface, add one narrow
terminal-renderer adapter for Ink 7.1 fullscreen cursor placement, and expose an
explicit product error for incompatible Application state. Do not add data
migration, automatic deletion, a fixed-bottom input area, or an alternate-screen
UI.

## Problem statement

Two independent failures currently produce poor developer experience:

1. The real terminal cursor is correct while Ink appends a trailing newline to
   an inline frame, but moves one row upward after the rendered output fills the
   terminal. The visible result is a cursor on the `Message` title row from the
   second sufficiently tall Turn onward.
2. Application SQLite Schema 1 is intentionally unsupported by the current
   Schema 2 implementation, but initialization converts the resulting
   `ApplicationSchemaMismatch` into the generic JSON-RPC diagnostic
   `core_request_failed`. The user cannot distinguish disposable stale state
   from a Core defect.

These failures must be fixed at their owning boundaries. Composer layout must
not infer Turn count, and the TUI must not inspect SQLite.

## Design principles

- Preserve the inline terminal and native scrollback behavior already selected
  for Awesome.
- Keep logical cursor placement independent from Ink's physical frame
  convention.
- Treat incompatible state as an explicit, non-retryable product condition.
- Never mutate or delete state while diagnosing startup.
- Do not reinterpret Schema 1 or add a compatibility migration.
- Keep renderer measurements and animation state out of Application, protocol,
  and persistence models.
- Test the real boundary that failed instead of only testing pure coordinate
  arithmetic.

## Part 1: Ink fullscreen cursor bridge

### Confirmed failure mechanism

Ink 7.1 renders an inline frame in two forms:

```text
outputHeight < terminalRows   -> output + trailing newline
outputHeight >= terminalRows  -> output without trailing newline
```

Ink's cursor helper calculates movement as if the physical cursor were on the
line after the last visible output line. That assumption is true in the first
form and one row too low in the fullscreen form. The helper therefore moves the
cursor one additional row upward when the frame fills the terminal.

The existing `Composer` calculation remains the logical source of truth: its
measured top plus border/title offsets and its display-width-aware row and
column identify the correct position inside the Ink output. No Turn or
transcript-specific branch belongs in `Composer`.

### Ownership and components

Introduce a presentation-only renderer boundary:

```text
TerminalSurfaceLayout
  -> measures current Ink frame height
  -> reads current terminal row count
  -> provides TerminalFrameMetrics to descendants

Composer
  -> calculates logical output-relative cursor position

InkCursorBridge
  -> converts logical position to the coordinate expected by Ink 7.1
  -> calls useCursor()
```

`TerminalFrameMetrics` contains only:

```text
frame_height
terminal_rows
has_measured
```

It is React presentation context, not Surface state. It is recalculated after
layout changes and terminal resize and is never persisted or sent over the
protocol.

`InkCursorBridge` applies one explicit renderer rule:

```text
non-fullscreen frame -> physical y = logical y
fullscreen frame     -> physical y = logical y + 1
```

The rule is based solely on Ink's frame contract
`frame_height >= terminal_rows`. It must not inspect Thread count, transcript
length, active Turn state, or command type. The adapter must name Ink 7.1 in a
focused comment and test so a future Ink upgrade can remove it deliberately.

### Cursor ownership

The existing exclusive input ownership remains:

- Composer and Command Menu may publish the Composer cursor;
- submitting, Picker, Approval, Trust, Auth, Secret, and Fatal modes hide it;
- no additional `useInput` subscriber is introduced;
- IME preedit remains owned by the terminal host.

### Rejected alternatives

- `hasHistory ? y + 1 : y`: encodes a renderer bug as product state and fails
  under resize or short/long Turns.
- fixed-bottom Composer or alternate screen: changes the accepted scrollback
  and conversation layout.
- fake block cursor: breaks host IME anchoring.
- vendored Ink fork, Git dependency, or `patch-package`: adds release and
  dependency maintenance disproportionate to the current product stage.
- direct ANSI writes from Composer: bypasses Ink ownership and risks competing
  cursor writers.

### Cursor verification

Pure coordinate tests remain, but the acceptance gate is an interactive Ink
renderer test using a controlled TTY stream. It must cover:

- frame height below, equal to, and above terminal rows;
- a first short Turn followed by a second Turn that crosses the threshold;
- terminal resize across the threshold in both directions;
- wrapped Composer rows, hidden-above rows, CJK, combining marks, and emoji;
- Command Menu ownership and exclusive interaction hiding;
- final emitted ANSI cursor position on the input row, not merely the return
  value of a helper.

A Windows PowerShell Pinyin check remains manual host evidence because a test
PTY cannot operate the user's system IME.

## Part 2: Explicit incompatible-state diagnostic

### Error ownership

Storage continues to own schema detection and raises
`ApplicationSchemaMismatch(found, expected)` before returning a connection.
Application composition must not open the writable LangGraph checkpoint saver
before this preflight completes; checkpoint resources are acquired only during
successful activation after the Application schema is accepted.
Application initialization owns conversion of that storage condition into a
typed product failure:

```text
code: state_schema_incompatible
retryable: false
data:
  found_schema: integer
  expected_schema: integer
  state_directory: string
```

The conversion happens at the Application initialization boundary. Storage
does not know terminal copy, and JSON-RPC does not infer exception types.

JSON-RPC serializes the normal `ApplicationResult` failure. It must not reach
the broad unexpected-exception handler or become `core_request_failed`.

### TUI behavior

Initialization maps `state_schema_incompatible` to a dedicated fatal state and
renders one actionable panel:

```text
Awesome state is incompatible with this version.

Found schema 1 · Expected schema 2
Close Awesome and reset the state directory before starting again:
<resolved state directory>

› Quit
```

There is no Reconnect action because repeating the same request cannot repair
the state. The TUI does not delete files, run shell commands, or claim that
configuration was removed.

### Recovery boundary

For `awesome-dev`, documentation instructs the developer to stop Awesome and
remove only:

```text
<repository>/.awesome-dev/home/state
```

This preserves `config.yaml` and `ui.json` while resetting disposable
conversation, checkpoint, trust, and Change Journal state. A custom
`AWESOME_HOME` uses its resolved `state` directory instead.

No automatic reset prompt is added. Destructive recovery remains an explicit
developer action after inspecting the displayed path.

### Schema verification

Tests must prove:

- a current Schema 2 database initializes normally;
- a Schema 1/unknown nonzero database returns the typed non-retryable product
  error with found and expected versions;
- the incompatible database, checkpoint state, and sibling configuration files
  are unchanged;
- protocol producer and TypeScript consumer agree on the exact error payload;
- the TUI displays the dedicated panel and offers only Quit;
- `core_request_failed` is reserved for genuinely unexpected request failures;
- developer Quickstart and troubleshooting documentation describe the scoped
  state reset without presenting it as migration.

## Delivery sequence

### PR 1: Correct fullscreen cursor placement

- add terminal frame metrics at the Ink layout boundary;
- add the narrow `InkCursorBridge`;
- retain current Composer and input ownership semantics;
- add real below/equal/above-viewport renderer regression tests;
- perform Windows host IME acceptance when available.

### PR 2: Make schema incompatibility actionable

- map `ApplicationSchemaMismatch` to `state_schema_incompatible`;
- add the exact protocol contract and TUI fatal state;
- remove the generic initialization path for this known condition;
- update English and Chinese developer recovery documentation;
- add storage-to-protocol-to-TUI regression coverage.

PR 2 does not depend on PR 1 semantically, but they are executed in this order
because the cursor defect currently affects every continued interactive
session.

## Out of scope

- SQLite migration or Schema 1 compatibility;
- automatic state deletion, backup, or rollback;
- preserving disposable test conversations;
- changing installed-product upgrade policy;
- alternate-screen rendering, fixed input regions, transcript virtualization,
  or scrollback redesign;
- custom Ink distribution or upstream dependency publishing.

## Acceptance criteria

- The cursor remains on the Composer input row before and after output fills
  the terminal and after terminal resize.
- Pinyin preedit uses that cursor anchor; committing text inserts it once.
- No Turn-count or transcript-length cursor branch exists.
- Schema mismatch never appears as `core_request_failed`.
- The mismatch panel states found/expected versions and the exact state
  directory, offers only Quit, and performs no mutation.
- Reset documentation deletes only the development `state` directory and
  preserves configuration.
- No migration, compatibility adapter for Schema 1, duplicate cursor writer,
  or temporary debug output remains.
