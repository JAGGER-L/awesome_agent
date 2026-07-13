# TUI Command and Activity Visual Consistency Design

## Status

Approved visual direction: **Scheme A** from
`awesome-interaction-system-v1.html`.

This document defines the product behavior to implement. The HTML is a visual
reference only. Terminal layout must use Ink display-width calculations and the
repository Logo constants rather than browser pixel measurements.

## Goals

- Make submitted slash commands visually identical to other user messages.
- Replace generic JSON command rendering with typed, command-specific results.
- Restore every previously approved command, picker, folding, and status layout.
- Keep terminal interaction predictable at narrow and wide widths.
- Preserve useful current-session Tool and Thinking detail without duplicating
  sensitive raw content in durable storage.

## Non-goals

- No compatibility aliases or legacy Presenter fallback.
- No new slash commands.
- No complete raw Tool output persistence.
- No change to Agent reasoning, Tool execution, or command semantics unrelated
  to presentation and typed result contracts.
- No redesigned ASCII Logo glyphs.

## Visual Foundation

### Welcome Scheme A

Welcome remains two adjacent bordered panels at wide terminal widths.

- The left panel width is the display width of the selected Logo rows plus
  border and horizontal padding.
- The right panel receives all remaining width and keeps the existing ordered
  status fields.
- The Logo is rendered directly from `FULL_LOGO_ROWS` or `COMPACT_LOGO_ROWS`.
- Each Logo row uses `theme.logoRows[index]`.
- The implementation must not scale, duplicate, trim, or retype Logo content.
- Below the existing responsive breakpoint, the panels stack vertically.

The browser mockup's slight Logo misalignment is not an implementation target;
it is caused by browser font metrics. Ink tests must assert terminal display
width at 80, 100, and 120 columns.

### Shared Visual Roles

- User input: mint user color with `❯ ` prefix.
- Assistant message: assistant color with the existing assistant marker.
- Result panel: rounded border, semantic title, consistent horizontal padding.
- Progress: colored side rail and compact background region.
- Success: success symbol and success color.
- Warning and danger: shared warning/danger panel variants.
- Folded detail: one summary line with an explicit `Ctrl+O` hint.
- Worked summary: a bounded pill-like status line, not muted transcript prose.

Color is never the only distinction; symbols, labels, borders, and text remain
present in no-color terminals.

## Slash Command Transcript Semantics

Every submitted slash command immediately creates a user transcript block with
the exact submitted text:

```text
❯ /context
```

This applies to:

- Python Application commands;
- local Ink commands such as `/help`, `/theme`, `/copy`, and `/quit`;
- commands that open a Picker or Secret input;
- commands whose Picker is later cancelled;
- commands that fail validation or execution.

The command result is a separate block after the user block. A command must
never be represented only by a pale command-result title.

Slash command user blocks are session transcript UI records, not conversation
messages sent to the model.

## Catalog, Completion, Menu, and Help

Command metadata separates four concepts:

- `name`: canonical command identity, for example `resume`;
- `completion`: executable insertion text, for example `/resume`;
- `usage`: help-only syntax, for example `/resume [thread_id]`;
- `description`: one concise user-facing sentence.

Tab completion inserts `completion` only. It never inserts optional argument
notation such as `[thread_id]`, `[provider]`, or `[on|off]`.

The command menu contains the complete registered command catalog. It uses a
ten-row viewport rather than truncating the result set:

- Up and Down move through every matching command.
- The viewport scrolls to keep the selection visible.
- The footer shows the visible range and total result count.
- Tab completes the selected canonical command.
- Enter submits it.
- Esc closes the menu.
- Menu rows show canonical command names without argument placeholders.

`/help` renders one command per row. The left column shows help `usage`; the
right column shows `description`. `/help <command>` may show additional details
but must not expose internal ownership enums.

## Typed Command Result Boundary

The public command result contract must no longer use one arbitrary
`Record<string, JsonValue>` as the sole payload. Python and TypeScript define a
discriminated command payload union with exact producer-consumer fixtures.

Required payload families:

- context snapshot;
- usage snapshot;
- memory state and memory choices;
- status snapshot;
- doctor diagnostics;
- workspace path;
- Tool catalog;
- Skill catalog and selection;
- MCP status;
- configuration summary;
- Diff result;
- Undo/Redo change result;
- compact result;
- thread replacement;
- permissions and credential interactions.

One exhaustive Presenter boundary consumes typed payloads and produces terminal
view models. The built-in command set uses a compile-time exhaustive mapping,
not a runtime registration framework. It must not use `Object.entries`,
implicit object stringification, `Array.join` on object arrays, or JSON
serialization as a normal user-facing fallback.

## Command-specific Behavior

### `/context`

Do not display the raw Context Manifest. Aggregate its token estimates into
these exact user-facing categories:

- Instructions: product, model identity, workspace instructions, and Skill;
- Conversation: Thread summary, recent Turns, direct commands, current input,
  and an open Tool chain;
- Files: explicit path snapshots;
- Memory: local user/workspace memory and Mem0 recall.

Display each category on its own row with right-aligned binary K/M values,
followed by the summed total and configured context budget.

Internal source IDs, hashes, ordering values, and coverage sequences are not
shown in ordinary `/context` output.

### `/usage`

Each metric occupies one row. Numeric values share a right-aligned column.
Token values use binary K/M formatting. Durations remain explicit time values.

### `/workspace`

Display only the normalized current workspace path. Do not display Trust or
the workspace key because command execution is unavailable before trust.

### `/status`

Render the approved status rows inside a rounded `Status` panel. Preserve
human-readable permission names and do not expose internal enum formatting.

### `/doctor`

Render one diagnostic per row inside a dedicated panel. Status values align in
one column. Nested Provider diagnostics must be mapped to explicit rows rather
than stringified objects.

### `/memory`

With no arguments, open a first-level Picker:

- Local memory;
- Cloud memory · Mem0.

Selecting a layer opens an On/Off Picker. The current value is selected. If
Mem0 credentials are unavailable, enabling Cloud memory returns an actionable
message directing the user to `/auth`; it does not silently fall back.

### `/compact`

Preserve the approved text and in-place lifecycle:

```text
❯ /compact

◇ Compressing context...
```

The same progress block becomes:

```text
✓ Context compressed
```

or a specific failure result. The progress block uses the shared status rail so
it cannot be mistaken for ordinary history text.

### `/diff`

Render an explicit empty state when no ChangeSet exists. A real Diff uses the
shared result panel and terminal Markdown/code styling.

### `/undo` and `/redo`

Success shows operation, affected file count, and lifecycle. File paths and
per-path outcomes are folded by default and participate in global `Ctrl+O`.

Errors remain specific:

- ChangeSet not found;
- workspace conflict;
- not reversible;
- invalid lifecycle;
- unexpected internal failure.

Application code must not collapse these into a broad `except Exception` and
the generic message `Change operation failed.`

### `/tools`

Each Tool occupies one row. The command shows the current Tool catalog and
permission-related state without assuming the catalog is permanently limited
to the initial built-ins.

## Thinking, Tool Sequence, and Worked Lifecycle

### Thinking

Live reasoning text remains visible while reasoning is active. When the
reasoning interval closes, it becomes one folded block:

```text
◇ Thought for 2.1 s · Ctrl+O to expand
```

Global `Ctrl+O` expands or folds all current transcript details, including
Thinking, Tool sequences, Undo, and Redo. There is no focus-dependent detail
selection because terminal history has no stable cursor ownership.

Reasoning duration is measured from local reasoning event boundaries. It never
claims Provider-internal timing when no reasoning events exist.

### Tool Sequence

All Tool calls between two Assistant segments remain one sequence. The folded
sequence remains exactly one line. Expanded tools show:

- verb and target;
- outcome;
- bounded summary;
- local duration;
- bounded presentation detail;
- explicit truncation count when known.

For `ls`, expanded detail lists returned entry paths and types, followed by
`… +N entries` when truncated.

Current-session reconciliation must preserve safe presentation detail instead
of replacing it with summary-only hydrated records. Durable ToolActivity storage
continues to exclude raw file bodies and complete Shell output. On resume,
durable safe summaries remain available even when ephemeral detail is not.

### Worked

Turn total duration renders as a distinct bounded status component:

```text
✦ Worked for 2.2 s
```

In color terminals it uses one padded line with the secondary foreground,
bold `✦`, and a dedicated status background token. In no-color terminals it
renders `[Worked] 2.2 s`. It is separated from adjacent transcript content by
one blank row and cannot be confused with muted Assistant prose.

## State Ownership

- Python Application owns command semantics and typed command payloads through
  one command dispatch path. Composition and alternate hosts may wire or invoke
  this path but may not reimplement command behavior.
- Protocol owns exact serialized types and nullability.
- The exhaustive Presenter maps typed payloads to terminal view models.
- Surface store owns transcript blocks, including slash-command user blocks.
- Terminal UI state owns only transient selection, input, and global detail
  mode.
- Ink components own borders, spacing, colors, alignment, and responsive layout.

No component may reconstruct Application truth from formatted strings.

## Error and Cancellation Behavior

- A submitted command remains in history after failure or Picker cancellation.
- Cancellation closes the active interaction and restores Composer focus.
- Every command produces a visible result, Picker, progress state, or error.
- Internal codes and enums are mapped to user-facing text at the Presenter
  boundary.
- Protocol contract failures remain fatal and are not swallowed.

## Acceptance Tests

### Exact rendering

At 80, 100, and 120 columns, assert exact output for:

- Welcome Scheme A, including Logo row identity and panel widths;
- slash command user blocks;
- Context, Usage, Status, Doctor, Workspace, Memory, Compact, Diff, Undo, Redo,
  Tools, Skills, MCP, and Config;
- Thinking folded/expanded;
- Tool sequence folded/expanded with List entries;
- Worked duration component;
- success, warning, danger, empty, progress, and error states.

### Keyboard behavior

Cover:

- `/` opening the complete catalog;
- Up/Down scrolling beyond the first ten entries;
- Tab inserting canonical completion only;
- Enter execution;
- Esc cancellation;
- global Ctrl+O detail mode;
- nested Memory Picker selection and focus restoration.

### Cross-boundary contracts

Generate Python fixtures for every command payload variant and parse them with
the TypeScript schemas. Include object-array cases so `[object Object]` cannot
reappear.

### Product flows

Exercise submitted slash history, Picker cancellation, Compact replacement,
Memory toggling, Context rendering, Status panel, Tool detail reconciliation,
Thinking completion, Undo/Redo details, and continued conversation after
cancellation.

Tests that only assert non-empty JSON, serialized length, or generic visibility
do not satisfy this acceptance contract.
