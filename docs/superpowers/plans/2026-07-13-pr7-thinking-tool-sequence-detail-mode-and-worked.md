# PR7 Thinking, Tool Sequence, Detail Mode, and Worked Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve and clearly present the complete current-session Agent activity lifecycle—Thinking, assistant segments, Tool sequences, and Worked duration—without adding sensitive durable history.

**Architecture:** Store bounded reasoning text on each live Thinking interval, project Tools into assistant-bounded sequences, and reconcile terminal live activity with durable Turn confirmation while retaining safe ephemeral display detail. Reuse PR6's global `detailsExpanded` and `ExpandableDetails`; keep durable ToolActivity summary-only and omit resumed Thinking/Worked details that were never persisted.

**Tech Stack:** Python 3.12 Tool presentation/events, Protocol v2, TypeScript, React, Ink, Vitest, pytest.

## Global Constraints

- PR1–PR6 must be merged before execution.
- Branch as `codex/pr7-agent-activity-lifecycle` and merge to the integration branch.
- Do not persist raw reasoning text, Tool presentation detail, complete file bodies, complete shell output, or UI fold state.
- Do not add an Event Store or change SQLite ToolActivity schema.
- Live reasoning appears while its interval is active. Completed reasoning becomes one folded `Thought for <duration> · Ctrl+O to expand` block.
- Thinking duration is measured only from local reasoning event timestamps; never claim Provider-internal reasoning duration.
- No Thinking block appears when no reasoning delta occurred.
- Every Tool call between two Assistant segments belongs to one Tool Sequence, regardless of Tool name and regardless of intervening Thinking intervals.
- A folded Tool Sequence is exactly one terminal row. Expanded mode shows every Tool's verb, target, outcome, summary, duration, bounded detail, and known truncation count.
- `ls` expanded detail lists actual type/path entries from Core presentation detail.
- Current-session reconciliation preserves safe live details by `call_id`; resumed Threads use durable summaries only.
- Durable outcome/status/summary wins over live data during reconciliation; safe live verb/target/detail enriches it.
- Worked uses local Turn duration from the terminal event, is visually distinct, and is absent after resume when not persisted.
- Reuse one `detailsExpanded` value; do not add per-block focus or independent expansion flags.
- Repeated diagnostic aggregation is not a substitute for fixing identity collisions. PR2's unique key invariant remains mandatory.
- After identity correctness is restored, genuinely repeated runtime warnings
  with the same stable code and normalized message are represented by one
  session-only diagnostic block with a count. Distinct warnings are never
  merged, and React development warnings are never ingested as product data.

---

## Canonical State and Blocks

```ts
export interface ThinkingProjection {
  readonly kind: "thinking";
  readonly id: string;
  readonly started_at: string;
  readonly text: string;
  readonly duration_ms?: number;
}

export interface ThinkingBlock extends BlockBase {
  readonly kind: "thinking";
  readonly text: string;
  readonly duration_ms?: number;
}

export interface WorkedBlock extends BlockBase {
  readonly kind: "worked";
  readonly duration_ms: number;
}

export interface ToolGroupBlock extends BlockBase {
  readonly kind: "tools";
  readonly items: readonly ToolItem[];
}
```

Remove `TurnProjection.reasoning_text` and `LiveTranscriptProjection.reasoning_text`; the timeline is the only reasoning owner.

## Task 0: Prepare the PR7 Branch

**Files:**
- Create during execution: `.codex/pr-bodies/pr7-agent-activity-lifecycle.md`

- [ ] **Step 1: Update and branch**

```powershell
git switch codex/tui-command-visual-consistency
git pull --ff-only
git status --short --branch
git switch -c codex/pr7-agent-activity-lifecycle
```

Expected: clean PR7 branch.

## Task 1: Verify the Safe Tool Presentation Boundary from PR1

**Files:**
- Verify: `src/awesome_agent/core/tools/contracts.py`
- Verify: `src/awesome_agent/core/tools/builtins/listing.py`
- Verify: `src/awesome_agent/application/events.py`
- Verify: `src/awesome_agent/core/events.py`
- Verify: Protocol v2 event fixtures and Python/TypeScript contract tests
- Verify: SQLite ToolActivity models and storage tests

**Interfaces:**
- Consumes: PR1 optional `detail_truncated_count` contract.
- Produces: recorded evidence that exact known omitted-entry count is available in live Tool terminal events only.

- [ ] **Step 1: Write failing List and event tests**

List 5 entries with `max_entries=2` and assert summary `2 entries`, two detail lines, and `detail_truncated_count == 3`. Assert ToolActivity serialization/storage has no detail or truncation field.

- [ ] **Step 2: Run focused tests and stop on upstream deviation**

```powershell
uv run pytest tests/unit/core/tools tests/unit/application/test_events.py tests/unit/protocol/test_contract_fixtures.py tests/unit/storage/test_conversation_storage.py -q
```

Expected: PASS because PR1 owns this Protocol v2 contract. If it fails, stop PR7 and correct the merged PR1 deviation through a scoped prerequisite PR rather than adding a second implementation here.

- [ ] **Step 3: Inspect producer projection and persistence boundary**

Confirm the count is emitted only when greater than zero, event projection copies it, and ToolActivityDraft/SQLite columns remain unchanged.

- [ ] **Step 4: Record the passing evidence without a commit**

```powershell
uv run pytest tests/unit/core/tools tests/unit/application/test_events.py tests/unit/protocol/test_contract_fixtures.py tests/unit/storage/test_conversation_storage.py -q
npm --prefix tui test -- --run tests/contracts/fixtures.test.ts tests/protocol
```

Expected: PASS. Record the commands and results in this plan; do not create an empty commit.

## Task 2: Make Thinking Intervals Own Their Bounded Text

**Files:**
- Modify: `tui/src/state/model.ts`
- Modify: `tui/src/state/reducer.ts`
- Modify: `tui/src/transcript/model.ts`
- Modify: `tui/src/transcript/live.ts`
- Modify: `tui/src/transcript/reasoning.ts`
- Modify: `tui/src/components/transcript/ActiveTurn.tsx`
- Modify: `tui/src/components/transcript/blocks/BlockView.tsx`
- Modify: `tui/tests/state/reducer.test.ts`
- Modify: `tui/tests/transcript/reasoning.test.ts`
- Modify: `tui/tests/transcript/live.test.ts`
- Modify: `tui/tests/components/transcript.test.tsx`

**Interfaces:**
- Consumes: reasoning deltas and PR6 `detailsExpanded`.
- Produces: interval-owned live/completed Thinking blocks.

- [ ] **Step 1: Write failing interval tests**

Sequence: reasoning A → Tool → reasoning B → Assistant. Assert two Thinking intervals retain distinct text and locally measured durations. Active interval shows text; completed intervals fold by default; Ctrl+O shows bounded text. Terminal completion must not clear interval text from the current-session projection.

- [ ] **Step 2: Run tests and verify terminal disappearance**

```powershell
npm --prefix tui test -- --run tests/state/reducer.test.ts tests/transcript/reasoning.test.ts tests/transcript/live.test.ts tests/components/transcript.test.tsx
```

Expected: FAIL because reasoning is one Turn-level string and ActiveTurn returns null when terminal.

- [ ] **Step 3: Move reasoning text into active timeline interval**

On reasoning delta, append with `appendReasoningTail()` to the last open Thinking interval or create `{id, started_at, text}`. Tool/assistant/terminal events close only the last interval by setting duration. Remove the Turn-level string.

- [ ] **Step 4: Render live and folded Thinking through BlockView**

Active interval renders `Thinking...` plus current text. Completed collapsed form renders `Thought for 2.1 s · Ctrl+O to expand`; expanded form includes text. `ActiveTurn` continues rendering terminal live blocks until reconciliation commits them; terminal status alone no longer returns `null`.

- [ ] **Step 5: Run tests and commit**

```powershell
npm --prefix tui test -- --run tests/state/reducer.test.ts tests/transcript/reasoning.test.ts tests/transcript/live.test.ts tests/components/transcript.test.tsx
git add tui/src/state tui/src/transcript tui/src/components/transcript tui/tests/state tui/tests/transcript tui/tests/components/transcript.test.tsx
git commit -m "feat: retain completed thinking intervals"
```

Expected: PASS.

## Task 3: Group and Render Assistant-bounded Tool Sequences

**Files:**
- Modify: `tui/src/transcript/model.ts`
- Modify: `tui/src/transcript/live.ts`
- Modify: `tui/src/components/transcript/blocks/BlockView.tsx`
- Create: `tui/src/components/transcript/ToolSequence.tsx`
- Create: `tui/tests/components/tool-sequence.test.tsx`
- Modify: `tui/tests/transcript/live.test.ts`

**Interfaces:**
- Consumes: timeline Assistant boundaries, typed Tool items, and `detailsExpanded`.
- Produces: exactly one folded row per Tool sequence and detailed expansion.

- [ ] **Step 1: Write failing mixed-Tool sequence tests**

Timeline: Assistant → List → Read → Thinking → Grep → Assistant → Write → Assistant. Assert two Tool Sequence blocks with 3 and 1 Tools. The first collapsed output occupies one row and does not show individual tools. Expanded output shows all actual Tool names/targets/details in order.

- [ ] **Step 2: Write List detail/truncation rendering test**

Expanded List shows:

```text
● List src
  └ Listed · 2 entries · 16ms
     directory  src/awesome_agent
     file       src/main.py
     … +3 entries
```

Do not parse file content; split the Core display detail into bounded lines and preserve type/path text.

- [ ] **Step 3: Run tests and implement sequence projection/component**

```powershell
npm --prefix tui test -- --run tests/components/tool-sequence.test.tsx tests/transcript/live.test.ts
```

Flush pending Tools only on Assistant boundary or timeline end; Thinking does not split a sequence. Folded summary includes Tool count, total local duration when all completed, Running when any active, and Ctrl+O hint.

- [ ] **Step 4: Run tests and commit**

```powershell
npm --prefix tui test -- --run tests/components/tool-sequence.test.tsx tests/transcript/live.test.ts tests/components/transcript.test.tsx
git add tui/src/transcript tui/src/components/transcript tui/tests/transcript/live.test.ts tui/tests/components
git commit -m "feat: group assistant-bounded tool sequences"
```

Expected: PASS.

## Task 4: Reconcile Durable Turns Without Erasing Ephemeral Detail

**Files:**
- Modify: `tui/src/transcript/reconcile.ts`
- Modify: `tui/src/transcript/hydrate.ts`
- Modify: `tui/src/surface/controller.ts`
- Modify: `tui/tests/transcript/reconcile.test.ts`
- Modify: `tui/tests/transcript/hydrate.test.ts`
- Modify: `tui/tests/surface/controller.test.ts`

**Interfaces:**
- Consumes: terminal live projection and confirmed durable Thread page.
- Produces: current-session transcript preserving safe activity while resume remains summary-only.

- [ ] **Step 1: Write failing reconciliation tests**

Assert completed current session retains Thinking, two Tool sequences, List detail, assistant segments, and Worked after durable confirmation. Assert each Tool `call_id` exists durably. Assert resumed hydration contains Tool summary but no detail, Thinking, or Worked.

- [ ] **Step 2: Run tests and verify detail loss**

```powershell
npm --prefix tui test -- --run tests/transcript/reconcile.test.ts tests/transcript/hydrate.test.ts tests/surface/controller.test.ts
```

Expected: FAIL because reconciliation currently retains only live Status blocks.

- [ ] **Step 3: Implement identity-based activity reconciliation**

Validate durable Turn status, assistant entry, and every Tool call. Build the terminal region from live activity order. For each Tool, take outcome, result summary, duration, and error from durable ToolActivity, then enrich with safe live verb, target, presentation outcome, detail, and truncation count by `call_id`. Validate concatenated live Assistant text against the durable assistant content before retaining live Assistant segments; if it differs, use the durable assistant block and retain only non-assistant activity.

- [ ] **Step 4: Keep resume deliberately summary-only**

`hydrateThreadPage()` continues to build Tool items only from durable fields. Do not add synthetic Thinking/Worked or a message claiming detail is available.

- [ ] **Step 5: Run tests and commit**

```powershell
npm --prefix tui test -- --run tests/transcript/reconcile.test.ts tests/transcript/hydrate.test.ts tests/surface/controller.test.ts tests/components/tool-sequence.test.tsx
git add tui/src/transcript tui/src/surface/controller.ts tui/tests/transcript tui/tests/surface/controller.test.ts
git commit -m "fix: preserve safe live activity after reconciliation"
```

Expected: PASS.

## Task 5: Add the Distinct Worked Component

**Files:**
- Modify: `tui/src/transcript/model.ts`
- Modify: `tui/src/transcript/live.ts`
- Create: `tui/src/components/transcript/Worked.tsx`
- Modify: `tui/src/components/transcript/blocks/BlockView.tsx`
- Modify: `tui/src/preferences/theme.ts`
- Create: `tui/tests/components/worked.test.tsx`
- Modify: `tui/tests/transcript/live.test.ts`

**Interfaces:**
- Consumes: locally measured terminal Turn `duration_ms`.
- Produces: `WorkedBlock` and approved bounded status styling.

- [ ] **Step 1: Write failing color/no-color/duration tests**

Color form contains `✻ Worked for 2.2 s`, one blank row separation, bold marker, secondary text, and dedicated status background token. No-color form is `[Worked] 2.2 s`. No block appears without terminal local duration.

- [ ] **Step 2: Run tests and implement component**

```powershell
npm --prefix tui test -- --run tests/components/worked.test.tsx tests/transcript/live.test.ts
```

Add a semantic `statusBackground` theme role for color-capable themes and no background for no-color. Do not call this reasoning time.

- [ ] **Step 3: Run tests and commit**

```powershell
npm --prefix tui test -- --run tests/components/worked.test.tsx tests/transcript/live.test.ts tests/components/transcript.test.tsx tests/preferences/theme.test.ts
git add tui/src/transcript tui/src/components/transcript tui/src/preferences/theme.ts tui/tests/components tui/tests/transcript/live.test.ts tui/tests/preferences/theme.test.ts
git commit -m "feat: distinguish completed turn duration"
```

Expected: PASS.

## Task 6: Aggregate Genuine Repeated Runtime Diagnostics

**Files:**
- Modify: `tui/src/state/model.ts`
- Modify: `tui/src/state/reducer.ts`
- Modify: `tui/src/transcript/live.ts`
- Modify: `tui/src/transcript/model.ts`
- Modify: `tui/src/components/transcript/blocks/BlockView.tsx`
- Modify: `tui/tests/state/reducer.test.ts`
- Modify: `tui/tests/components/transcript.test.tsx`

**Interfaces:**
- Consumes: stable runtime warning code/message and PR6 `detailsExpanded`.
- Produces: one session-only counted diagnostic block for one repeated warning signature.
- Does not consume: `console.error`, React key warnings, or raw arbitrary stderr.

- [ ] **Step 1: Write counted-warning tests**

Dispatch the same warning code and normalized message 18 times and assert one
block renders `x 18` with a Ctrl+O hint; expanded mode shows the bounded warning
detail once. Dispatch the same code with a different message and a different
code with the same message and assert they remain separate blocks. Assert
Thread replacement clears session warning counts.

- [ ] **Step 2: Prove the current deduplication loses count**

```powershell
npm --prefix tui test -- --run tests/state/reducer.test.ts tests/components/transcript.test.tsx
```

Expected before implementation: repeated warnings remain at count one with no
expandable representation.

- [ ] **Step 3: Implement stable-signature aggregation**

Store `count` and bounded first/last occurrence detail on the existing Surface
warning record. Increment only for an exact stable code plus normalized message
signature. Project it through one diagnostic transcript block and reuse global
Ctrl+O for detail. Do not add a second diagnostic store or persistent record.

- [ ] **Step 4: Verify aggregation and identity independence**

```powershell
npm --prefix tui test -- --run tests/state/reducer.test.ts tests/components/transcript.test.tsx tests/transcript/identity.test.ts
```

The duplicate-assistant-key regression must still pass by unique identities;
warning aggregation must not hide or intercept that failure.

- [ ] **Step 5: Commit diagnostic aggregation**

```powershell
git add tui/src/state tui/src/transcript tui/src/components/transcript tui/tests/state/reducer.test.ts tui/tests/components/transcript.test.tsx
git commit -m "feat: aggregate repeated terminal diagnostics"
```

## Task 7: Verify PR7 and Merge

**Files:**
- Modify: `docs/architecture/protocol-and-ink.md`
- Modify: `docs/user-guide/commands.md`
- Modify: this plan for evidence only

- [ ] **Step 1: Document activity and persistence boundaries**

Document Thinking interval timing, Tool Sequence boundaries, Ctrl+O, safe ephemeral details, summary-only resume, Worked local duration, and the distinction between counted runtime diagnostics and development-time React failures.

- [ ] **Step 2: Run Python safety gates**

```powershell
uv run ruff format --check src tests
uv run ruff check src tests
uv run mypy
uv run pytest tests/unit/core/tools tests/unit/application/test_events.py tests/unit/protocol/test_contract_fixtures.py tests/unit/storage/test_conversation_storage.py -q
```

Expected: PASS.

- [ ] **Step 3: Run TUI gates**

```powershell
npm --prefix tui run format:check
npm --prefix tui run lint
npm --prefix tui run typecheck
npm --prefix tui run build
npm --prefix tui test -- --run tests/state tests/transcript tests/surface tests/components tests/interaction tests/preferences/theme.test.ts
git diff --check
```

Expected: PASS.

- [ ] **Step 4: Prove persistence did not expand**

```powershell
rg -n "reasoning_text|presentation_detail|detail_truncated_count" src/awesome_agent/conversation src/awesome_agent/storage
```

Expected: no durable model/schema/storage matches for reasoning or presentation detail. The optional truncation field appears only in Tool presentation/event code and fixtures.

- [ ] **Step 5: Commit docs and merge**

```powershell
git add docs/architecture/protocol-and-ink.md docs/user-guide/commands.md docs/superpowers/plans/2026-07-13-pr7-thinking-tool-sequence-detail-mode-and-worked.md
git commit -m "docs: describe agent activity lifecycle"
git push -u origin codex/pr7-agent-activity-lifecycle
$prUrl = gh pr create --base codex/tui-command-visual-consistency --head codex/pr7-agent-activity-lifecycle --title "feat: preserve terminal agent activity" --body-file .codex/pr-bodies/pr7-agent-activity-lifecycle.md
$prNumber = gh pr view codex/pr7-agent-activity-lifecycle --json number --jq .number
gh pr checks $prNumber --watch
gh pr merge $prNumber --merge --delete-branch
git switch codex/tui-command-visual-consistency
git pull --ff-only
```

## PR7 Completion Gate

- Live Thinking remains visible and completed Thinking folds with measured duration.
- Mixed Tools between Assistant segments form one sequence and collapse to one row.
- Expanded Tool details include real bounded List entries and known truncation counts.
- Current-session reconciliation does not erase safe details.
- Resume uses durable summaries only and never invents unavailable details.
- Worked is distinct from assistant/history text and reports only local Turn duration.
- Ctrl+O controls Thinking, Tool sequences, Undo, and Redo through one state value.
- Repeated genuine runtime diagnostics fold by stable signature and count; distinct diagnostics remain distinct.
- React identity warnings are fixed at the source and never converted into product diagnostics.
- No raw reasoning or complete Tool output is newly persisted.
- PR7 is merged before PR8 is executed.
