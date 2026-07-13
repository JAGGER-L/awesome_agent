# PR4 Semantic Command Presenters and Result Components Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render every informational command from PR1 semantic facts through one exhaustive Presenter and a small shared set of consistent, aligned terminal result components.

**Architecture:** Keep domain interpretation in Python payloads and terminal layout in TypeScript. Split pure payload-to-view-model Presenters from Ink components, use one bordered `ResultPanel` and width-aware `AlignedRows`, and provide explicit notice, empty, warning, and error variants without any generic object formatting fallback.

**Tech Stack:** TypeScript, React, Ink, Vitest, ink-testing-library, Protocol v2 semantic payloads.

## Global Constraints

- PR1–PR3 must be merged before execution.
- Branch as `codex/pr4-semantic-command-results` and target `codex/tui-command-visual-consistency`.
- Consume PR1 typed fields directly. Do not derive Provider source, Tool approval, Context budget, or product status by joining unrelated TUI state.
- Do not change Python command semantics or Protocol v2 in PR4; if a required fact is absent despite the amended PR1 contract, stop instead of guessing in TUI.
- Do not add a runtime Presenter registry, `unknown` formatter, object serializer, or catch-all view.
- The Presenter switch must be compile-time exhaustive over every `CommandPayload.kind`.
- Shared visual primitives are limited to `ResultPanel`, `AlignedRows`, `ResultNotice`, and `EmptyResult`; do not build a generic terminal component framework.
- Every command result is visually distinct from user and assistant messages through border, title, symbol, or semantic status text.
- Color is supplementary. No-color output retains border, title, labels, status words, and symbols.
- Context and Usage token values use binary units: 1,024 → `1K`, 262,144 → `256K`, 1,048,576 → `1M`.
- Values in Context, Usage, Status, and Doctor align to a common right edge at 80, 100, and 120 columns.
- At narrow widths, a row may wrap value beneath its label rather than truncate essential facts.
- `/workspace` shows only the normalized path and never Trust or workspace key.
- `/tools` renders every current Tool on its own row and does not assume the catalog remains fixed at eight Tools.
- PR6 owns final Compact, Diff, Undo, and Redo lifecycle styling; PR4 keeps their typed exhaustive cases functional without redesigning them.

---

## Canonical View Models

```ts
export interface PresentationRow {
  readonly label: string;
  readonly value: string;
  readonly status?: "normal" | "success" | "warning" | "danger";
}

export type CommandPresentation =
  | {
      readonly kind: "panel";
      readonly title: string;
      readonly rows: readonly PresentationRow[];
      readonly tone: "info" | "success" | "warning" | "danger";
    }
  | {
      readonly kind: "notice";
      readonly message: string;
      readonly tone: "info" | "success" | "warning";
    }
  | {
      readonly kind: "empty";
      readonly title: string;
      readonly message: string;
    }
  | {
      readonly kind: "progress";
      readonly message: string;
      readonly tone: "info" | "success" | "danger";
    }
  | {
      readonly kind: "markdown";
      readonly title: string;
      readonly source: string;
      readonly tone: "info" | "warning";
    }
  | {
      readonly kind: "error";
      readonly title: string;
      readonly message: string;
    };
```

`progress` and `markdown` remain because PR6 consumes them; no unused domain payload is added.

Presenter signature:

```ts
export function presentCommandPayload(
  command: CommandName,
  payload: CommandPayload,
): CommandPresentation;

export function presentCommandError(
  command: CommandName,
  code: string,
  message: string,
): CommandPresentation;
```

## Task 0: Prepare the PR4 Branch

**Files:**
- Create during execution: `.codex/pr-bodies/pr4-semantic-command-results.md`

- [ ] **Step 1: Update integration and create branch**

```powershell
git switch codex/tui-command-visual-consistency
git pull --ff-only
git status --short --branch
git switch -c codex/pr4-semantic-command-results
```

Expected: clean PR4 branch.

## Task 1: Replace Legacy Presentation Shapes with an Exhaustive View Model

**Files:**
- Modify: `tui/src/commands/presenters.ts`
- Modify: `tui/tests/commands/presenters.test.tsx`
- Modify: `tui/tests/transcript/command-results.test.ts`

**Interfaces:**
- Consumes: Protocol v2 `CommandPayload`.
- Produces: canonical `CommandPresentation` and exhaustive Presenter functions.

- [ ] **Step 1: Write failing exhaustive and forbidden-fallback tests**

Create one typed payload fixture per `kind`, call `presentCommandPayload`, and assert a concrete presentation. Add a compile-time helper:

```ts
function assertNever(value: never): never {
  throw new Error(`Unhandled command payload kind: ${String(value)}`);
}
```

Source assertions reject `Object.entries`, `JSON.stringify`, `String(record`, `Array.join` for object arrays, `isRecord`, and `unknown` parameters in Presenter code.

- [ ] **Step 2: Run Presenter tests and verify legacy generic path**

```powershell
npm --prefix tui test -- --run tests/commands/presenters.test.tsx tests/transcript/command-results.test.ts
```

Expected: FAIL until the new view-model union and exhaustive switch exist.

- [ ] **Step 3: Implement pure exhaustive mapping**

Switch on `payload.kind`. Route Context, Usage, Workspace, Status, Doctor, Tools, Skills, MCP, Config, Notice, Thinking, Model, Thread, Memory, Diff, Change, and Compact to explicit functions. The default branch calls `assertNever(payload)`.

- [ ] **Step 4: Run tests and commit**

```powershell
npm --prefix tui test -- --run tests/commands/presenters.test.tsx tests/transcript/command-results.test.ts
git add tui/src/commands/presenters.ts tui/tests/commands/presenters.test.tsx tui/tests/transcript/command-results.test.ts
git commit -m "refactor: make command presentation exhaustive"
```

Expected: PASS.

## Task 2: Build the Shared Result Primitives

**Files:**
- Create: `tui/src/components/results/ResultPanel.tsx`
- Create: `tui/src/components/results/AlignedRows.tsx`
- Create: `tui/src/components/results/ResultNotice.tsx`
- Create: `tui/src/components/results/EmptyResult.tsx`
- Create: `tui/src/components/results/index.ts`
- Modify: `tui/src/components/CommandResultView.tsx`
- Create: `tui/tests/components/results/result-panel.test.tsx`
- Create: `tui/tests/components/results/aligned-rows.test.tsx`
- Modify: `tui/tests/components/transcript.test.tsx`

**Interfaces:**
- Consumes: Task 1 `CommandPresentation`.
- Produces: four shared terminal primitives and one exhaustive `CommandResultView`.

- [ ] **Step 1: Write failing exact rendering tests at 80, 100, and 120 columns**

Assert rounded panel border, title, padding, semantic symbol/text, right-aligned values, narrow wrapping, and no-color labels. Duplicate labels must still receive stable position-based React keys and render twice.

- [ ] **Step 2: Run component tests and verify missing primitives**

```powershell
npm --prefix tui test -- --run tests/components/results tests/components/transcript.test.tsx
```

Expected: FAIL because the result component directory does not exist.

- [ ] **Step 3: Implement ResultPanel and AlignedRows**

`ResultPanel` uses Ink `borderStyle="round"`, theme border/tone colors, one horizontal padding cell, and width clamped to at least 20 columns. `AlignedRows` uses `justifyContent="space-between"` at sufficient width and a two-line label/value layout when display widths plus two spaces exceed the available inner width.

- [ ] **Step 4: Implement notices, empty state, and exhaustive view**

Symbols are fixed and color-independent:

```text
● info
✓ success
! warning
× danger/error
◇ empty
```

`CommandResultView` switches on every `CommandPresentation.kind`; it does not inspect command payloads.

- [ ] **Step 5: Run tests and commit**

```powershell
npm --prefix tui test -- --run tests/components/results tests/components/transcript.test.tsx
git add tui/src/components tui/tests/components
git commit -m "feat: add shared command result components"
```

Expected: PASS.

## Task 3: Implement Context and Usage Presenters

**Files:**
- Modify: `tui/src/commands/presenters.ts`
- Create: `tui/tests/commands/context-usage-presenters.test.tsx`

**Interfaces:**
- Consumes: `ContextCommandPayload`, `UsageCommandPayload`, and `formatTokenCount()`.
- Produces: exact aligned rows.

- [ ] **Step 1: Write failing exact row tests**

Context rows in this order:

```text
Instructions    12K
Conversation  4.5K
Files          1.2K
Memory          640
Total         18.3K
Budget          256K
```

Usage rows in this order:

```text
Input tokens
Output tokens
Reasoning tokens
Cache read tokens
Cache write tokens
Model calls
Tool calls
Provider retries
Compressions
Active execution
```

Every value occupies a shared right-aligned column; only token values use K/M. Active execution uses a measured duration string such as `2.2s`.

- [ ] **Step 2: Run tests and verify current adjacency**

```powershell
npm --prefix tui test -- --run tests/commands/context-usage-presenters.test.tsx
```

Expected: FAIL until exact rows and right alignment are implemented.

- [ ] **Step 3: Implement formatting**

`formatTokenCount()` uses 1024 units and at most one decimal. `formatDuration()` uses milliseconds below one second, one decimal seconds below one minute, and `Xm Ys` above one minute. Do not label local duration as Provider reasoning time.

- [ ] **Step 4: Run tests and commit**

```powershell
npm --prefix tui test -- --run tests/commands/context-usage-presenters.test.tsx tests/commands/presenters.test.tsx
git add tui/src/commands/presenters.ts tui/tests/commands
git commit -m "feat: present context and usage clearly"
```

Expected: PASS.

## Task 4: Implement Workspace, Status, and Doctor Presenters

**Files:**
- Modify: `tui/src/commands/presenters.ts`
- Create: `tui/tests/commands/status-doctor-presenters.test.tsx`

**Interfaces:**
- Consumes: `WorkspaceCommandPayload`, expanded `StatusSnapshot`, and `DoctorCommandPayload` from PR1.
- Produces: normalized path notice and bordered Status/Doctor panels.

- [ ] **Step 1: Write failing exact Status rows**

Status contains one row per category in this order:

```text
Version
Workspace
Thread
Model
Credentials
Permissions
Context
Thinking
Skill
Memory
MCP
Operation
Changes
```

Rules:

- Thread uses title plus resumable display ID.
- Model uses effective model identity.
- Credentials shows `Environment`, `Awesome`, `Not configured`, or the selected source plus `Unavailable`; never silently reports another source.
- Permissions maps enums to `Request approval` or `Full access`.
- Context shows formatted used/budget.
- Memory shows `Local On/Off · Cloud Mem0 On/Off`.
- Operation shows `Idle` or a value such as `Active · operation_abcd1234`.
- Changes shows `None` or a value such as `3 files modified`.

- [ ] **Step 2: Write Doctor and Workspace tests**

Doctor renders every check as its own row with aligned `OK`, `Missing`, `Valid`, `Invalid`, `Unverified`, `Off`, or `Error`. Workspace output contains only the normalized path and excludes `trusted`, workspace key, and Git branch.

- [ ] **Step 3: Run tests, implement explicit mapping, and commit**

```powershell
npm --prefix tui test -- --run tests/commands/status-doctor-presenters.test.tsx
git add tui/src/commands/presenters.ts tui/tests/commands/status-doctor-presenters.test.tsx
git commit -m "feat: present status and diagnostics panels"
```

Expected: PASS at 80, 100, and 120 columns.

## Task 5: Implement Tools, Skills, MCP, and Config Presenters

**Files:**
- Modify: `tui/src/commands/presenters.ts`
- Create: `tui/tests/commands/catalog-presenters.test.tsx`

**Interfaces:**
- Consumes: typed catalog/config payloads.
- Produces: one-row-per-item panels and explicit empty states.

- [ ] **Step 1: Write failing typed list tests**

Assert:

- every Tool is one row in Core order with `Enabled` or `Approval required` from `approval_required`;
- Skills show active mode first and one row per Skill, followed by explicit diagnostics;
- MCP shows one row per server and `No MCP servers configured` when empty;
- Config shows source layers and one credential row each for DeepSeek, Kimi, and Mem0 without exposing secret values;
- repeated values do not collapse React rows.

- [ ] **Step 2: Run tests and implement explicit list mappings**

```powershell
npm --prefix tui test -- --run tests/commands/catalog-presenters.test.tsx
```

Implement one typed mapper per payload. Do not share an object-key-driven mapper.

- [ ] **Step 3: Run tests and commit**

```powershell
npm --prefix tui test -- --run tests/commands/catalog-presenters.test.tsx tests/commands/presenters.test.tsx tests/components/results
git add tui/src/commands/presenters.ts tui/tests/commands
git commit -m "feat: present tools and extension status"
```

Expected: PASS.

## Task 6: Verify PR4 and Merge

**Files:**
- Modify: `docs/architecture/protocol-and-ink.md`
- Modify: this plan for evidence only

- [ ] **Step 1: Document semantic Presenter and shared primitives**

Document Core facts → Protocol payload → exhaustive Presenter → shared result component. State that TUI never formats arbitrary JSON.

- [ ] **Step 2: Run quality and render gates**

```powershell
npm --prefix tui run format:check
npm --prefix tui run lint
npm --prefix tui run typecheck
npm --prefix tui run build
npm --prefix tui test -- --run tests/commands tests/components/results tests/components/transcript.test.tsx tests/transcript/command-results.test.ts
rg -n "Object\.entries|JSON\.stringify|isRecord|result\.data|\[object Object\]" tui/src/commands tui/src/components/results
git diff --check
```

Expected: tests PASS and forbidden Presenter fallback search has no matches.

- [ ] **Step 3: Commit documentation**

```powershell
git add docs/architecture/protocol-and-ink.md docs/superpowers/plans/2026-07-13-pr4-semantic-command-presenters-and-result-components.md
git commit -m "docs: define semantic terminal results"
```

- [ ] **Step 4: Push, PR, and merge**

Create `.codex/pr-bodies/pr4-semantic-command-results.md`, then:

```powershell
git push -u origin codex/pr4-semantic-command-results
$prUrl = gh pr create --base codex/tui-command-visual-consistency --head codex/pr4-semantic-command-results --title "feat: render semantic command results" --body-file .codex/pr-bodies/pr4-semantic-command-results.md
$prNumber = gh pr view codex/pr4-semantic-command-results --json number --jq .number
gh pr checks $prNumber --watch
gh pr merge $prNumber --merge --delete-branch
git switch codex/tui-command-visual-consistency
git pull --ff-only
```

## PR4 Completion Gate

- No command Presenter accepts or formats arbitrary JSON.
- `/context` cannot emit `[object Object]` and shows four categories, total, and budget.
- `/usage` shows one aligned metric per row with correct K/M units.
- `/workspace` shows only its normalized path.
- `/status` and `/doctor` render bordered, aligned, user-readable panels.
- `/tools`, `/skills`, `/mcp`, and `/config` show one typed item per row and explicit empty states.
- Credential availability and Tool approval are rendered from Core facts, not inferred by TUI.
- Result components remain readable at 80, 100, and 120 columns and without color.
- PR4 is merged before PR5 is executed.

## Execution Evidence

- Implemented one exhaustive typed Presenter and removed the replaced
  `lines`, `picker`, and `secret` presentation variants.
- Added the four shared result primitives and exhaustive Ink rendering.
- Added focused Presenter and component coverage for Context, Usage, Workspace,
  Status, Doctor, Tools, Skills, MCP, Config, empty states, duplicate rows,
  narrow wrapping, and 80/100/120-column inputs.
- Verified with `npm --prefix tui run format:check`, `lint`, `typecheck`,
  `build`, and the affected command/result/transcript Vitest suites.
- Confirmed the Presenter/result source contains no `Object.entries`,
  `JSON.stringify`, `isRecord`, `result.data`, or `[object Object]` fallback.
