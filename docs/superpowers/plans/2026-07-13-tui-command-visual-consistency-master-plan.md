# TUI Command and Activity Visual Consistency Master Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver one coherent, typed, recoverable terminal interaction system in which Python owns product semantics, Ink owns presentation, every submitted command has visible lifecycle feedback, and the approved Scheme A UI is implemented without parallel legacy paths.

**Architecture:** Preserve the current Python Core + private stdio JSON-RPC + Ink TUI topology. Consolidate command behavior behind the existing Application dispatch boundary, replace arbitrary command JSON with a small set of exact semantic result families, project every command through one transcript and one exhaustive Presenter, and keep durable, session, and transient UI state under distinct owners.

**Tech Stack:** Python 3.12, Pydantic, LangGraph, SQLite, JSON-RPC/NDJSON over stdio, TypeScript, React, Ink, Zod, pytest, Vitest.

## Global Constraints

- The product remains a Local-first Coding Agent, not a general Agent platform or reusable terminal UI framework.
- Python remains the product Core; TypeScript is limited to the Ink TUI and protocol consumer.
- The Ink TUI never calls models, runs tools, writes product configuration, or reconstructs Application truth from formatted strings.
- No LangGraph server, HTTP service, PostgreSQL, worker, event store, Redux, XState, or runtime Presenter plugin system is introduced.
- No compatibility aliases, legacy result fallback, temporary adapter, swallowed exception, or parallel old/new implementation is retained.
- No new production dependency is added unless a child PR plan proves it is required and the user approves it.
- The approved UI source is Scheme A in `awesome-interaction-system-v1.html`; browser pixel measurements are not terminal layout contracts.
- Welcome uses repository Logo constants without retyping, scaling, trimming, or replacing glyphs.
- The complete raw Tool output and raw reasoning content are not added to durable storage.
- Every submitted slash command is recorded immediately as a user-style transcript block, including commands that later fail or are cancelled.
- The active credential source never falls back silently when an explicitly selected source is unavailable.
- Every command produces a visible result, progress state, interaction, cancellation result, or actionable error.
- Each PR starts from and merges back into `codex/tui-command-visual-consistency`; `main` is not the integration target until all eight PRs are complete and the final acceptance gate passes.
- Each PR removes the production path it replaces in the same PR; no follow-up cleanup PR is used to preserve two authorities temporarily.
- Validation remains risk-based: formatting and lint, type checking, affected unit tests, structural contracts, affected integration tests, then cross-component flow tests.

---

## 1. Target Product Invariants

The implementation is complete only while all of these invariants remain true:

1. **One product truth:** Thread, Provider, model, permissions, memory state, credentials, active operation, and command semantics are owned by Python Application state.
2. **One command authority:** all hosts invoke the same command dispatcher and domain handlers; composition and headless entry points do not implement competing behavior.
3. **One protocol meaning:** Python serialization and TypeScript parsing agree exactly on discriminator, fields, nullability, enums, numbers, and nested collections.
4. **One transcript path:** user commands, command results, progress, notices, Thinking, Tool sequences, and Worked summaries are rendered from the active Thread surface rather than independent side channels.
5. **One input owner:** Fatal recovery, Trust, Approval, Secret input, Picker, Slash menu, and Composer have a fixed priority and cannot consume the same key simultaneously.
6. **One visual grammar:** shared terminal primitives express information, success, warning, danger, progress, selection, empty state, and expandable detail.
7. **Bounded persistence:** SQLite stores continuity, security, configuration, Change Journal, and safe execution summaries; ephemeral display detail is not promoted into an Event Store.
8. **Replaceable TUI:** another client could consume Application semantics without depending on Ink colors, borders, spacing, or display copy.

## 2. Target Execution Flow

```text
Submitted terminal input
        |
        v
Single input router
  Composer / Slash / Picker / Secret / Approval
        |
        v
Command controller
  - append exact user command immediately
  - invoke local command or typed RPC
        |
        v
Python Core command dispatcher
        |
        +-- Conversation commands
        +-- Context commands
        +-- Configuration commands
        +-- Extension commands
        +-- Change commands
        `-- Diagnostic commands
        |
        v
Typed CommandOutcome
  Result / Interaction / Error
        |
        v
Compile-time exhaustive Presenter
        |
        v
TerminalBlock[] in active Thread surface
        |
        v
Ink layout components
```

Agent turns retain their separate model/tool event path, but both paths converge at the same Thread surface before rendering.

## 3. State Ownership

| State class | Sole owner | Examples | Explicit exclusions |
| --- | --- | --- | --- |
| Durable product state | Python Application + SQLite/checkpoint | Threads, final Turns, summaries, Provider preference, permissions, Memory/Skill/MCP enablement, Change Journal, safe Tool summaries | Raw reasoning, complete file bodies, complete shell output, UI fold state |
| Active application state | Python Application | One foreground operation, pending interaction, cancellation, active Thread, active credential source | Terminal focus, selected menu row, colors |
| Session surface projection | TUI surface reducer | Current Thread blocks, streaming segments, ephemeral Thinking detail, ephemeral Tool presentation detail, transcript generation | Configuration authority, Provider resolution, tool policy |
| Transient terminal state | Ink controller/reducer | Composer text, focus owner, Picker index, Slash viewport offset, secret input, global detail mode, terminal width | Thread truth, permissions, Memory state, active operation truth |

Thread replacement is an atomic surface transition. `/new` and `/resume` must not mutate a shared list while leaving blocks from the previous generation visible.

## 4. Module Responsibility Map

### Python Application

| Area | Target responsibility |
| --- | --- |
| `application/dispatcher.py` | Resolve a typed intent to exactly one handler and return a typed outcome. No product-specific duplicate fallback path. |
| Command domain services | Own command semantics grouped by conversation, context, configuration, extensions, changes, and diagnostics. |
| `application/composition.py` | Construct dependencies and connect handlers. It does not format user output or implement commands. |
| `application/facade.py` | Provide the only surface-facing Application API and delegate to the composed backend. |
| `application/commands.py` and related contracts | Define canonical command identity, intent, outcome envelope, semantic payloads, interactions, and errors. |
| Protocol host | Serialize Application contracts and enforce protocol errors. It does not repair malformed results. |

Do not create one class per command. Child plans should prefer a small number of cohesive domain services and keep files reviewable.

### TypeScript TUI

| Area | Target responsibility |
| --- | --- |
| `protocol/commands.ts` and focused schema modules | Parse exact Python results with discriminators and strict nullability. |
| `commands/catalog.ts` | Be the only metadata source for command name, completion, usage, description, ownership, and visibility. |
| `commands/controller.ts` | Coordinate command execution and typed interaction transitions. |
| `commands/presenters.ts` or focused presenter modules | Exhaustively convert semantic results to terminal view models. No arbitrary JSON display fallback. |
| `surface/` and `transcript/` | Own ordered, stable, generation-aware visible blocks and live/durable reconciliation. |
| `app/App.tsx` | Compose controllers, stores, and terminal views. Product workflows move out of component conditionals. |
| `components/` | Render borders, alignment, colors, responsive layout, and keyboard hints from view models. |

The Presenter is a static, compiler-checked mapping for the built-in command set. A runtime Presenter registry is explicitly out of scope.

## 5. Command Result Contract Strategy

The current arbitrary `data: Record<string, JsonValue>` is replaced by a discriminated `CommandOutcome` envelope:

```text
CommandOutcome
  result       semantic command result
  interaction  Picker / secret / confirmation request
  error        stable code plus safe user-facing context
```

Command progress is a Surface lifecycle while an RPC is pending, not a returned
protocol variant. For example, `/compact` creates a typed local progress block
before `command.execute` and replaces that same block with the final result or
error. No unused protocol progress type is introduced.

The result side uses a bounded set of product-semantic families rather than one UI schema per row and rather than one untyped JSON container:

- Context snapshot;
- Usage snapshot;
- Status snapshot;
- Doctor diagnostics;
- Memory state;
- Tool, Skill, and MCP catalogs;
- Workspace path;
- Change and Diff results;
- Thread replacement;
- Configuration and credential summary;
- Compact and general notice result.

These payloads contain facts, not terminal instructions. Border kind, column width, color token, spacing, and glyph choice remain TUI responsibilities.

## 6. Interaction Priority

The terminal has exactly one input owner at a time in this order:

```text
Fatal recovery
Trust confirmation
Approval / permission escalation
Secret input
Picker
Slash command menu
Composer
Global lifecycle keys
```

- `Enter` confirms or submits only through the active owner.
- `Esc` cancels only the highest-priority active owner and restores the previous valid owner.
- Up/Down belong to the active Picker or Slash viewport before Composer history.
- `Tab` completes the selected canonical slash command without usage placeholders.
- `Ctrl+O` toggles one global transcript-detail mode for Thinking, Tool sequences, Undo, and Redo.
- `Ctrl+C` cancels the active operation before any exit behavior.

## 7. Durable and Ephemeral Detail Policy

### Persist

- Tool name and call identity;
- bounded input and outcome summaries;
- success/error/cancelled status;
- measured local duration;
- affected paths/counts when safe;
- final assistant output and conversation records required for resume.

### Keep only for the active process/session

- live reasoning text;
- bounded Tool presentation detail such as the visible `ls` entries;
- partially assembled streaming blocks;
- fold state and current viewport;
- temporary progress animation state.

### Never persist through this project

- API keys or secret input history;
- raw complete file bodies solely for TUI expansion;
- raw complete shell output solely for TUI expansion;
- Provider-private reasoning or invented reasoning duration.

After resume, safe durable summaries remain visible. Missing ephemeral detail is not reconstructed or silently claimed to exist.

## 8. Approved Visual Contract

The implementation must preserve all decisions in `docs/superpowers/specs/2026-07-13-tui-command-visual-consistency-design.md`, especially:

- Welcome Scheme A with intrinsic Logo panel width and repository Logo/color tokens;
- exact submitted slash command rendered as a mint user block with the normal user prefix;
- ten-row scrolling Slash viewport over the complete catalog;
- Tab completion without `[thread_id]`, `[provider]`, or other usage notation;
- `/help` one command per aligned row;
- bordered Status and Doctor panels;
- aligned Context and Usage numeric columns with binary K/M units;
- `/workspace` showing only the normalized path;
- nested `/memory` Picker for Local and Cloud memory;
- visible, in-place `/compact` progress-to-result transition;
- folded Undo/Redo details;
- one folded Tool Sequence spanning all Tool calls between two Assistant segments;
- completed Thinking folded to measured duration;
- distinct Worked component;
- Scheme A ordinary Picker and warning/danger semantic variant;
- color-independent symbols and text for no-color terminals.

HTML remains a design reference. Exact terminal acceptance is defined by Ink rendering tests at 80, 100, and 120 columns.

## 9. PR Sequence

Each PR below must receive its own detailed implementation plan before code is changed. The child plan must name exact files, interfaces, failing tests, commands, expected failures, implementation steps, deletion steps, validation, and commit boundary.

### PR1: Single Command Authority and Typed Contracts

**Objective:** Establish the architectural foundation used by every later PR.

**In scope:**

- Audit and consolidate duplicate command semantics behind the existing Application dispatcher.
- Reduce composition to wiring and make headless/facade paths delegate to the same handlers.
- Introduce the typed outcome envelope and bounded semantic result families.
- Make Python serialization and TypeScript Zod parsing agree through real fixtures.
- Preserve existing user-visible behavior except where the accepted contract intentionally replaces it.
- Delete the generic producer/consumer paths replaced in this PR.

**Acceptance gate:**

- One command has one semantic handler regardless of entry point.
- Object arrays, nullable fields, floating durations, enums, interactions, and errors cross the Python-TypeScript boundary exactly.
- No normal command result relies on arbitrary object stringification.
- Existing Agent, Tool, storage, and Provider behavior remains outside the change.

**Dependency:** None.

### PR2: Transcript Command Semantics and Thread Replacement

**Objective:** Make the transcript the only visible command lifecycle and make Thread switching atomic.

**In scope:**

- Append every exact submitted slash command immediately as a user-style block.
- Use stable, unique segment and block identities.
- Route local and Core command results through the same surface semantics.
- Move `/status` out of independent component state and into transcript results.
- Make `/new` and `/resume` replace the active transcript generation without old Thread blocks.
- Remove replaced status and transcript special paths.

**Acceptance gate:**

- Submitted commands remain visible after success, failure, or cancellation.
- No duplicate React key warning occurs during multiple assistant segments.
- New and resumed Threads never mix visible blocks.
- The user command is not sent to the model as a conversation message.

**Dependency:** PR1 typed outcomes.

### PR3: Command Catalog, Completion, Menu, and Help

**Objective:** Make one catalog drive discoverability, completion, help, and dispatch metadata.

**In scope:**

- Separate canonical name, completion, usage, and description.
- Replace result truncation with a ten-row scrolling viewport.
- Make Up/Down, Tab, Enter, and Esc deterministic under the single-owner model.
- Render `/help` as one aligned command row per entry.
- Remove stale, duplicate, and unsupported command metadata paths.

**Acceptance gate:**

- Every registered visible command can be reached by keyboard beyond row ten.
- Tab never inserts argument placeholders.
- Catalog, autocomplete, Help, and dispatcher identity cannot drift independently.

**Dependency:** PR2 transcript command blocks.

### PR4: Semantic Command Presenters and Shared Result Components

**Objective:** Replace generic command rendering with exact, consistent terminal results.

**In scope:**

- Implement Context, Usage, Workspace, Status, Doctor, Tools, Skills, MCP, Config, and empty/result views.
- Introduce only the shared visual primitives needed by approved designs: panel, aligned rows, status badge, notice, and empty state.
- Format large tokens with binary K/M units and aligned numeric/status columns.
- Delete generic `Object.entries`, object `join`, and JSON user-facing fallback rendering.

**Acceptance gate:**

- `/context` cannot render `[object Object]`.
- Every listed command produces an exact typed view or explicit empty/error state.
- Status and Doctor use bordered panels and do not expose internal enums.
- Exact output is tested at representative widths.

**Dependency:** PR1 result families and PR2 transcript result blocks.

### PR5: Unified Picker, Memory, Auth, and Permissions

**Objective:** Give all interactive commands one focus-safe interaction system.

**In scope:**

- Implement the approved base Picker and warning/danger variants.
- Implement nested Local/Cloud `/memory` selection and On/Off transitions.
- Preserve the approved `/auth` Provider-then-source flow for DeepSeek, Kimi, and Mem0 Cloud.
- Represent Environment availability, Awesome credential availability, explicit active source, replacement, deletion, validation, and unavailable selected source without silent fallback.
- Apply the approved permissions status and escalation behavior.
- Ensure secret values never enter transcript, logs, project files, or protocol diagnostics.
- Remove per-command input and focus logic replaced by shared interaction state.

**Acceptance gate:**

- Exactly one component owns Enter/Esc/Up/Down at every interaction step.
- Save, replace, delete, cancel, invalid credential, and unavailable-source flows provide explicit feedback and restore Composer focus.
- Memory enablement without Mem0 credentials points to `/auth`.
- Environment credentials remain read-only and are never presented as modified by Awesome.

**Dependency:** PR1 interactions, PR2 transcript semantics, PR4 shared result components.

### PR6: Compact, Diff, Undo, and Redo Lifecycles

**Objective:** Make progress and Change Journal commands visible, specific, and recoverable.

**In scope:**

- Render `/compact` as one progress block replaced in place by success or specific failure.
- Render Diff content and an explicit no-change state.
- Render Undo/Redo summaries with globally foldable path details.
- Preserve specific ChangeSet failure categories across Application, protocol, Presenter, and transcript.
- Delete broad generic change-operation error handling and replaced command paths.

**Acceptance gate:**

- Compact never creates disconnected duplicate lifecycle lines.
- Undo/Redo expose correct affected-file summaries and bounded details.
- Not found, conflict, non-reversible, invalid lifecycle, and unexpected failures remain distinguishable.

**Dependency:** PR1 typed change results, PR2 block replacement, PR4 result primitives.

### PR7: Thinking, Tool Sequence, Detail Mode, and Worked

**Objective:** Present the complete Agent activity lifecycle without persisting unnecessary sensitive detail.

**In scope:**

- Fold completed Thinking into measured local duration while preserving current-session expansion.
- Group all Tool calls between Assistant segments into one sequence, independent of Tool name.
- Show bounded Tool-specific details, including actual `ls` entry paths and truncation counts.
- Preserve safe current-session presentation detail during live-to-hydrated reconciliation.
- Replace Tool-only expansion state with one global detail mode covering Thinking, Tool sequences, Undo, and Redo.
- Render Worked as the approved distinct status component.

**Acceptance gate:**

- Completed Thinking does not disappear.
- A multi-tool chain folds to exactly one line and expands deterministically.
- Reconciliation does not erase safe detail during the active session.
- Resume shows durable summaries without inventing unavailable detail.
- No raw reasoning, file body, shell output, or secret is newly persisted.

**Dependency:** PR2 stable transcript identities and PR6 global fold consumers.

### PR8: Welcome Scheme A, Documentation, and Full Interaction Regression

**Objective:** Finish the approved visual system and prove the complete terminal workflow.

**In scope:**

- Implement Welcome Scheme A using terminal display width and repository Logo/theme constants.
- Verify responsive wide and stacked layouts.
- Update user and developer documentation for final command behavior, keyboard controls, Memory/Auth/Permissions, development startup, and known limitations.
- Keep `README.md` and `README.zh-CN.md` behaviorally consistent.
- Run the complete targeted command and interaction regression matrix.
- Remove obsolete tests, snapshots, components, and plan artifacts replaced by the completed architecture.

**Acceptance gate:**

- Welcome matches the approved structure at 80, 100, and 120 columns without HTML-derived spacing hacks.
- Enter, Tab, Esc, Up/Down, Ctrl+O, and Ctrl+C behave consistently across all owners.
- All registered commands have visible success, progress, interaction, empty, cancellation, or failure behavior.
- `/new`, `/resume`, `/auth`, `/memory`, Approval, streaming, Markdown, cancellation, and continued conversation pass targeted end-to-end flows.
- Documentation describes the current implementation rather than the implementation plan.

**Dependency:** PR1-PR7.

## 10. Cross-PR Acceptance Matrix

| Requirement | Owning PR | Final regression |
| --- | --- | --- |
| Single Python command authority | PR1 | PR8 structural and integration checks |
| Exact Python-TypeScript contracts | PR1 | PR8 fixture suite |
| Slash command as user history | PR2 | PR8 command flows |
| Atomic `/new` and `/resume` | PR2 | PR8 Thread flows |
| Stable unique transcript identities | PR2 | PR7 streaming and PR8 regression |
| Complete scrolling command menu | PR3 | PR8 keyboard matrix |
| Correct Tab completion and Help | PR3 | PR8 command audit |
| Context/Usage/Status/Doctor layout | PR4 | PR8 width matrix |
| Tools/Skills/MCP/Config results | PR4 | PR8 command audit |
| Memory Picker | PR5 | PR8 interaction flows |
| Auth source selection without silent fallback | PR5 | PR8 credential flows |
| Permission Picker variants | PR5 | PR8 Approval flows |
| Compact progress replacement | PR6 | PR8 command flows |
| Diff/Undo/Redo specific outcomes | PR6 | PR8 Change Journal flows |
| Thinking and Tool sequence folding | PR7 | PR8 activity flows |
| Global Ctrl+O detail mode | PR7 | PR8 keyboard matrix |
| Worked visual treatment | PR7 | PR8 render matrix |
| Welcome Scheme A | PR8 | PR8 exact renders |
| Documentation consistency | PR8 | PR8 docs review |

## 11. Validation Policy

Every child PR plan must define the smallest sufficient checks in this order:

1. Python formatting/lint for changed Python files;
2. TypeScript formatting/lint for changed TUI files;
3. Python and TypeScript type checks;
4. affected unit tests written against behavior and contracts;
5. Python-to-TypeScript fixture contracts when protocol changes;
6. affected Application/TUI integration tests;
7. focused Ink rendering and keyboard tests;
8. cross-component flow tests only when the PR crosses those boundaries.

Tests tied only to deleted implementation details are removed with the implementation. No skip, expected failure, permissive schema, exception swallowing, or compatibility adapter may be added solely to keep an obsolete test green.

## 12. Per-PR Delivery Workflow

For PR1 through PR8:

- [ ] Write and approve the child implementation plan before production code changes.
- [ ] Branch from the latest `codex/tui-command-visual-consistency` integration head.
- [ ] Implement only the child PR scope using test-first behavior checks.
- [ ] Remove replaced production logic and obsolete implementation-coupled tests.
- [ ] Run and record the child plan validation commands.
- [ ] Inspect diff and status for unrelated changes, secrets, generated caches, debug output, and stale plan artifacts.
- [ ] Commit one verified logical change set.
- [ ] Push and create a PR targeting `codex/tui-command-visual-consistency`.
- [ ] Merge only after the PR is scoped, conflict-free, and its required checks pass.
- [ ] Update the integration branch locally before writing or executing the next child plan.

## 13. Completion Definition

The master plan is complete only when:

- all eight child PRs are merged into `codex/tui-command-visual-consistency` in order;
- no replaced generic command-result, duplicate command-authority, separate status-display, or per-command input path remains;
- every accepted HTML interaction decision has a corresponding Ink behavior test;
- all registered commands pass the command audit and produce visible outcomes;
- targeted Python, protocol, TUI, integration, and end-to-end validation evidence is recorded;
- the integration branch is clean and recoverable;
- remaining limitations are documented explicitly rather than hidden behind silent behavior.

Merging the integration branch to `main` and publishing are separate release decisions after this completion definition is satisfied.

## 14. Child Plan Order

Create the detailed plans in this exact sequence:

1. `PR1-single-command-authority-and-typed-contracts`
2. `PR2-transcript-command-semantics-and-thread-replacement`
3. `PR3-command-catalog-completion-menu-and-help`
4. `PR4-semantic-command-presenters-and-result-components`
5. `PR5-unified-picker-memory-auth-and-permissions`
6. `PR6-compact-diff-undo-and-redo-lifecycles`
7. `PR7-thinking-tool-sequence-detail-mode-and-worked`
8. `PR8-welcome-documentation-and-regression`

No later child plan may redefine an interface owned by an earlier PR without first amending this master plan and the affected earlier child plan.
