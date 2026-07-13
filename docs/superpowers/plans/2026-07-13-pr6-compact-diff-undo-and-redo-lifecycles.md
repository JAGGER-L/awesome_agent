# PR6 Compact, Diff, Undo, and Redo Lifecycles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Compact and Change Journal commands one visible, replaceable, specific lifecycle with explicit empty/error states and globally foldable path details.

**Architecture:** Keep Change semantics and error mapping in Python's `ChangeCommandService`, while the TUI represents a pending Compact RPC as one replaceable Surface block and presents typed Diff/Change payloads. Generalize the existing Tool-only detail flag into one transcript detail mode, add one reusable `ExpandableDetails` component, and preserve the same result block identity from pending through terminal outcome.

**Tech Stack:** Python 3.12, pytest, TypeScript, React, Ink, Vitest, Protocol v2.

## Global Constraints

- PR1–PR5 must be merged before execution.
- Branch as `codex/pr6-change-command-lifecycles` and target the integration branch.
- Do not change Change Journal storage, filesystem mutation algorithms, or LangGraph compression semantics.
- `/compact` creates exactly one visible `Compressing context...` block and replaces that same block with `Context compressed` or a specific error.
- Progress is TUI Surface state while the synchronous RPC is pending; no Protocol progress variant is introduced.
- Compact result must not appear as ordinary assistant/history prose.
- `/diff` with no ChangeSet returns a successful empty Diff payload, not an error, silence, or a generic failure.
- Real Diff uses the existing terminal Markdown/code renderer and stays bounded by Core's existing maximum.
- `/undo` and `/redo` show action, affected file count, lifecycle, and warning; paths are folded by default.
- `Ctrl+O` toggles one global `detailsExpanded` value for all current transcript detail blocks.
- Errors remain distinct: ChangeSet not found, workspace conflict, not reversible, invalid lifecycle, unexpected internal failure.
- Unexpected failures are Protocol failures; do not catch `Exception` and convert it to `Change operation failed`.
- No compatibility `toolDetailsExpanded` state/action/prop remains after this PR.
- Do not implement Thinking or Tool Sequence redesign in PR6; PR7 consumes the generic detail interface.

---

## Canonical Interfaces

Interaction state:

```ts
export interface TerminalUiState {
  readonly mode: UiMode;
  readonly composer: ComposerState;
  readonly composerSubmitting: boolean;
  readonly detailsExpanded: boolean;
  readonly composerMessage?: string;
  readonly notice?: string;
}

export type TerminalUiAction =
  | { readonly type: "details.toggle" }
  // existing actions
```

Change presentation:

```ts
export type ChangePresentation = {
  readonly kind: "change";
  readonly title: "Undo" | "Redo";
  readonly changeSetId: string;
  readonly lifecycle: string;
  readonly paths: readonly string[];
  readonly warning?: string;
};
```

`CommandPresentation` adds `ChangePresentation`; `CommandResultView` receives `detailsExpanded` and renders it through `ExpandableDetails`.

## Task 0: Prepare the PR6 Branch

**Files:**
- Create during execution: `.codex/pr-bodies/pr6-change-command-lifecycles.md`

- [ ] **Step 1: Update and branch**

```powershell
git switch codex/tui-command-visual-consistency
git pull --ff-only
git status --short --branch
git switch -c codex/pr6-change-command-lifecycles
```

Expected: clean PR6 branch.

## Task 1: Preserve Exact Change Errors in the Application Boundary

**Files:**
- Modify: `src/awesome_agent/application/change_commands.py`
- Modify: `tests/unit/application/test_change_commands.py`
- Modify: `tests/integration/test_change_journal.py`

**Interfaces:**
- Consumes: PR1 `DiffCommandPayload`, `ChangeCommandPayload`, and typed errors.
- Produces: exact Change command outcomes without broad exception conversion.

- [ ] **Step 1: Write failing error-category tests**

Assert exact codes:

```python
assert missing.code == "change_set_not_found"
assert conflict.code == "workspace_conflict"
assert irreversible.code == "change_not_reversible"
assert lifecycle.code == "invalid_change_lifecycle"
```

Inject `RuntimeError("invariant")` and assert it propagates rather than becoming a command error.

- [ ] **Step 2: Run focused tests**

```powershell
uv run pytest tests/unit/application/test_change_commands.py tests/integration/test_change_journal.py -q
```

Expected: PASS if PR1 fully implemented the contract; otherwise FAIL identifies the exact remaining broad path and must be fixed here.

- [ ] **Step 3: Remove any remaining broad conversion and verify payload facts**

Diff returns ID and content. Undo/Redo return action, ID, lifecycle, exact restored paths, and optional warning from `ChangeOperationResult`.

- [ ] **Step 4: Run tests and commit**

```powershell
uv run pytest tests/unit/application/test_change_commands.py tests/integration/test_change_journal.py -q
git add src/awesome_agent/application/change_commands.py tests/unit/application/test_change_commands.py tests/integration/test_change_journal.py
git commit -m "fix: preserve change command outcomes"
```

Expected: PASS.

## Task 2: Generalize Global Transcript Detail State

**Files:**
- Modify: `tui/src/interaction/model.ts`
- Modify: `tui/src/interaction/reducer.ts`
- Modify: `tui/src/interaction/key-router.ts`
- Modify: `tui/src/app/App.tsx`
- Modify: `tui/src/components/transcript/Transcript.tsx`
- Modify: `tui/src/components/transcript/ActiveTurn.tsx`
- Modify: `tui/src/components/transcript/blocks/BlockView.tsx`
- Modify: related interaction/transcript tests

**Interfaces:**
- Consumes: existing Ctrl+O Tool detail behavior.
- Produces: `detailsExpanded` and `details.toggle` for all detail-bearing blocks.

- [ ] **Step 1: Write failing generic-detail tests**

Assert Ctrl+O routes `details.toggle`, reducer changes `detailsExpanded`, and Transcript/ActiveTurn/BlockView receive that prop. Assert no `toolDetailsExpanded` identifier remains.

- [ ] **Step 2: Run tests and verify old naming**

```powershell
npm --prefix tui test -- --run tests/interaction tests/components/transcript.test.tsx tests/transcript
```

Expected: FAIL on generic naming assertions.

- [ ] **Step 3: Rename the state/action/props as one atomic change**

Do not add a second flag. Existing Tool expansion behavior remains unchanged under the generic prop.

- [ ] **Step 4: Run tests and commit**

```powershell
rg -n "toolDetailsExpanded|tool_details\.toggle" tui/src tui/tests
npm --prefix tui test -- --run tests/interaction tests/components/transcript.test.tsx tests/transcript
git add tui/src tui/tests
git commit -m "refactor: generalize transcript detail mode"
```

Expected: search has no matches and tests PASS.

## Task 3: Add Expandable Change Results

**Files:**
- Create: `tui/src/components/results/ExpandableDetails.tsx`
- Modify: `tui/src/components/results/index.ts`
- Modify: `tui/src/commands/presenters.ts`
- Modify: `tui/src/components/CommandResultView.tsx`
- Modify: `tui/src/transcript/model.ts`
- Create: `tui/tests/components/results/expandable-details.test.tsx`
- Create: `tui/tests/commands/change-presenters.test.tsx`

**Interfaces:**
- Consumes: Task 2 `detailsExpanded` and typed Change payload.
- Produces: one folded summary and expanded path detail.

- [ ] **Step 1: Write failing collapsed/expanded tests**

Collapsed:

```text
✓ Undo · 3 files · Undone · Ctrl+O to expand
```

Expanded contains each path on its own row, ChangeSet ID, lifecycle, and warning. Redo uses `Redo` and `Applied`. Zero paths displays `0 files` without a phantom detail row.

- [ ] **Step 2: Run tests and verify missing change view**

```powershell
npm --prefix tui test -- --run tests/components/results/expandable-details.test.tsx tests/commands/change-presenters.test.tsx
```

Expected: FAIL until `ChangePresentation` and component exist.

- [ ] **Step 3: Implement reusable details and typed Change mapping**

`ExpandableDetails` takes summary React content, `expanded`, hint text, and detail children. It owns only layout; it does not inspect payloads or handle keys.

- [ ] **Step 4: Run tests and commit**

```powershell
npm --prefix tui test -- --run tests/components/results/expandable-details.test.tsx tests/commands/change-presenters.test.tsx tests/components/transcript.test.tsx
git add tui/src/components/results tui/src/components/CommandResultView.tsx tui/src/commands/presenters.ts tui/src/transcript/model.ts tui/tests/components/results tui/tests/commands/change-presenters.test.tsx
git commit -m "feat: add foldable change results"
```

Expected: PASS.

## Task 4: Implement Compact In-place Progress Replacement

**Files:**
- Modify: `tui/src/app/use-command-execution.ts`
- Modify: `tui/src/app/App.tsx`
- Modify: `tui/src/commands/presenters.ts`
- Modify: `tui/src/components/results/ResultNotice.tsx`
- Create: `tui/tests/app/compact-flow.test.tsx`
- Modify: `tui/tests/transcript/command-results.test.ts`

**Interfaces:**
- Consumes: PR2 stable command-result identity and PR1 `CompactCommandPayload`.
- Produces: one progress block replaced by terminal result.

- [ ] **Step 1: Write failing pending/success/failure tests**

While RPC is pending, transcript contains command input and one result block with `Compressing context...`. On success, the same result key contains `Context compressed`; on failure, the same key contains the specific error. At no point are both progress and terminal lines present.

- [ ] **Step 2: Run tests and verify visual/lifecycle weakness**

```powershell
npm --prefix tui test -- --run tests/app/compact-flow.test.tsx tests/transcript/command-results.test.ts
```

Expected: FAIL until the pending block uses the shared progress style and exact replacement identity.

- [ ] **Step 3: Implement start/finish lifecycle**

Start progress only for `/compact` after its command input block is recorded and before RPC. Capture the returned replacement closure/key. Finish exactly once with Presenter success or error. If Thread generation changes, reducer ignores the stale replacement.

- [ ] **Step 4: Run tests and commit**

```powershell
npm --prefix tui test -- --run tests/app/compact-flow.test.tsx tests/transcript/command-results.test.ts tests/components/app-command-flow.test.tsx
git add tui/src/app tui/src/commands/presenters.ts tui/src/components/results/ResultNotice.tsx tui/tests/app/compact-flow.test.tsx tui/tests/transcript/command-results.test.ts tui/tests/components/app-command-flow.test.tsx
git commit -m "feat: replace compact progress in place"
```

Expected: PASS.

## Task 5: Render Diff and Explicit Empty State

**Files:**
- Modify: `tui/src/commands/presenters.ts`
- Modify: `tui/src/components/CommandResultView.tsx`
- Create: `tui/tests/commands/diff-presenter.test.tsx`

**Interfaces:**
- Consumes: `DiffCommandPayload` and typed `change_set_not_found` only for an explicitly requested missing ID.
- Produces: Markdown Diff presentation or explicit empty result.

- [ ] **Step 1: Write failing empty and real Diff tests**

`DiffCommandPayload(change_set_id: undefined, content: "")` renders `◇ No workspace changes`. An explicitly requested missing ID renders the specific error. A real Diff renders title `Diff`, ChangeSet ID, file headers, added/removed lines, and bounded content through `MarkdownBlock` without exposing an object dump.

- [ ] **Step 2: Run test, implement explicit mapping, and commit**

```powershell
npm --prefix tui test -- --run tests/commands/diff-presenter.test.tsx tests/markdown
git add tui/src/commands/presenters.ts tui/src/components/CommandResultView.tsx tui/tests/commands/diff-presenter.test.tsx
git commit -m "feat: render change set diff results"
```

Expected: PASS.

## Task 6: Verify PR6 and Merge

**Files:**
- Modify: `docs/user-guide/commands.md`
- Modify: `docs/architecture/application-and-langgraph.md`
- Modify: this plan for evidence only

- [ ] **Step 1: Document command lifecycle and details**

Document Compact pending replacement, Diff empty state, specific Change errors, Undo/Redo summaries, and Ctrl+O global detail behavior.

- [ ] **Step 2: Run Python gates**

```powershell
uv run ruff format --check src tests
uv run ruff check src tests
uv run mypy
uv run pytest tests/unit/application/test_change_commands.py tests/integration/test_change_journal.py -q
```

Expected: PASS.

- [ ] **Step 3: Run TUI gates**

```powershell
npm --prefix tui run format:check
npm --prefix tui run lint
npm --prefix tui run typecheck
npm --prefix tui run build
npm --prefix tui test -- --run tests/app/compact-flow.test.tsx tests/commands tests/components/results tests/interaction tests/transcript tests/markdown
rg -n "toolDetailsExpanded|tool_details\.toggle|Change operation failed" src tui tests
git diff --check
```

Expected: tests PASS and forbidden legacy search has no matches.

- [ ] **Step 4: Commit docs and merge**

```powershell
git add docs/user-guide/commands.md docs/architecture/application-and-langgraph.md docs/superpowers/plans/2026-07-13-pr6-compact-diff-undo-and-redo-lifecycles.md
git commit -m "docs: describe change command lifecycles"
git push -u origin codex/pr6-change-command-lifecycles
$prUrl = gh pr create --base codex/tui-command-visual-consistency --head codex/pr6-change-command-lifecycles --title "feat: complete change command lifecycles" --body-file .codex/pr-bodies/pr6-change-command-lifecycles.md
$prNumber = gh pr view codex/pr6-change-command-lifecycles --json number --jq .number
gh pr checks $prNumber --watch
gh pr merge $prNumber --merge --delete-branch
git switch codex/tui-command-visual-consistency
git pull --ff-only
```

## PR6 Completion Gate

- Compact uses one strongly styled block from pending through success/failure.
- Diff has explicit empty and real-content paths.
- Undo/Redo summaries and paths are typed, specific, and foldable.
- Ctrl+O toggles one generic transcript detail mode.
- All Change error categories remain distinct and unexpected failures are not swallowed.
- No Tool-only detail state/action/prop or generic `Change operation failed` remains.
- PR6 is merged before PR7 is executed.

## Execution Evidence

- Confirmed ChangeCommandService already preserves all four domain error
  categories and lets unexpected failures propagate; no wrapper was added.
- Removed the Tool-only detail state/action/prop and replaced it atomically with
  one global transcript detail mode.
- Added reusable expandable details plus typed folded Undo/Redo presentations.
- Added a pending/success/failure Compact identity test proving in-place
  replacement rather than duplicate transcript lines.
- Added explicit empty and real Diff presentations with ChangeSet identity and
  terminal Markdown rendering.
