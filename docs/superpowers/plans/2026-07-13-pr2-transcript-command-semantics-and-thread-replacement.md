# PR2 Transcript Command Semantics and Thread Replacement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the active Thread transcript the only visible command lifecycle, record every submitted slash command immediately as user input, and replace Thread surfaces atomically without stale blocks or duplicate identities.

**Architecture:** Consume PR1 `CommandOutcome` payloads without adding product truth to React state. Add a dedicated slash-command input block to the Surface reducer, keep generation-aware transcript replacement as the Thread boundary, route Status through the normal command Presenter, and derive every stable React key from a submitted-command identity, durable entry identity, protocol call identity, or deterministic Turn segment ordinal.

**Tech Stack:** TypeScript, React, Ink, Zod contracts from PR1, Vitest, ink-testing-library.

## Global Constraints

- PR1 must already be merged into `codex/tui-command-visual-consistency` before this plan is executed.
- Branch from the latest integration head as `codex/pr2-transcript-thread-replacement`; merge back only to the integration branch.
- Do not change Python command semantics, Protocol v2 payload shapes, command visuals, command catalog metadata, Markdown behavior, or Tool/Thinking folding in this PR.
- Slash command input blocks are TUI session transcript records and are never written to the model conversation or SQLite conversation entries.
- Record the exact trimmed-left submitted command text, including arguments; do not reconstruct it from parsed tokens.
- Record a submitted slash command before parse validation, RPC execution, local command execution, Picker opening, or Secret input opening.
- A Picker cancellation leaves the submitted command block visible and restores Composer focus.
- Thread replacement is atomic: Application state, Thread projection, transcript blocks, warnings, active operation, pending interaction, and generation change together.
- Events, deltas, reconciliation, and command outcomes from an older generation cannot enter the new Thread surface.
- Do not use random keys during render. Generate an identity once when a block is created and preserve it through replacement/reconciliation.
- Do not reintroduce Ink `Static` for the replaceable active Thread transcript.
- `/status` is a command result block in transcript; no independent `useState<StatusSnapshot>` or standalone rendering path remains.
- `/new` displays exactly one `New conversation started` notice in the new empty Thread and no old blocks.
- `/resume` loads the selected Thread's durable transcript and does not inject the new-conversation notice.
- Delete every special path replaced in this PR; no legacy status or command-title-only rendering remains.

---

## Target File Responsibilities

### Create

- `tui/src/app/use-thread-replacement.ts` — load Application/Thread projections and commit one generation-checked atomic replacement.
- `tui/tests/app/thread-replacement.test.tsx` — `/new`, `/resume`, stale request, notice, and focus flows.
- `tui/tests/components/app-command-flow.test.tsx` — immediate submitted-command ordering across local and Core commands.
- `tui/tests/transcript/command-input.test.ts` — exact slash submission identity and non-model semantics.
- `tui/tests/transcript/key-invariants.test.ts` — stable uniqueness across assistant segments, commands, progress replacement, hydration, and reconciliation.

### Modify

- `tui/src/transcript/model.ts` — add `CommandInputBlock` and keep `CommandResultBlock` separate.
- `tui/src/transcript/identity.ts` — add `createCommandSubmissionId()`.
- `tui/src/state/actions.ts` — add `transcript.command.submitted`; retain generation on every transcript mutation.
- `tui/src/state/reducer.ts` — append command input, enforce generation, and keep atomic replacement reset semantics.
- `tui/src/transcript/merge.ts` — enforce stable-key merging without silent duplicate semantic identities.
- `tui/src/transcript/live.ts` and `tui/src/transcript/hydrate.ts` — preserve deterministic segment identities.
- `tui/src/app/use-command-execution.ts` — accept a command submission identity and keep result/progress blocks distinct from input.
- `tui/src/app/App.tsx` — submit command input immediately, use the thread replacement hook, and remove Status special state.
- `tui/src/components/transcript/blocks/BlockView.tsx` — render command input through the same visual role as a user message.
- `tui/src/components/transcript/Transcript.tsx` — receive only active Thread blocks; keep dynamic rendering.
- Existing state, transcript, command controller, and component tests.

### Delete

- `tui/src/components/StatusCommand.tsx` and `tui/tests/components/status-command.test.tsx` after Status is routed through `CommandResultBlock`.
- `status` React state, `setStatus()`, direct `statusSnapshotSchema.safeParse()` and conditional `<StatusCommand>` rendering from `App.tsx`.
- Any command result title that is the only visible representation of a submitted command.

## Canonical Interfaces

### Command input block

```ts
export interface CommandInputBlock extends BlockBase {
  readonly kind: "command_input";
  readonly submission_id: string;
  readonly text: string;
}

export type TranscriptBlock =
  | UserBlock
  | CommandInputBlock
  | AssistantBlock
  | DirectCommandBlock
  | ToolGroupBlock
  | ChangeSummaryBlock
  | ReasoningMarkerBlock
  | StatusBlock
  | CommandResultBlock
  | WarningBlock
  | ErrorBlock
  | OmittedHistoryBlock;
```

`text` includes the leading slash and exact arguments after `trimStart()`. A command block has no pending/accepted/persisted state because it is local UI history, not a model Turn.

### Command submission action

```ts
export type SurfaceAction =
  | {
      readonly type: "transcript.command.submitted";
      readonly submission_id: string;
      readonly text: string;
      readonly generation: number;
    }
  // existing actions remain explicit
```

Reducer behavior:

```ts
case "transcript.command.submitted":
  if (action.generation !== state.thread_generation) return state;
  return {
    ...state,
    committed_transcript: mergeTranscriptBlocks(
      state.committed_transcript ?? [],
      [{
        key: `command:${action.submission_id}`,
        kind: "command_input",
        submission_id: action.submission_id,
        text: action.text,
      }],
    ),
    transcript_persisted: false,
  };
```

### Stable identities

```ts
export function createCommandSubmissionId(): string {
  return `command_${randomUUID().replaceAll("-", "")}`;
}
```

Identity ownership:

| Block | Stable source |
| --- | --- |
| User Turn input | `client_message_id` |
| Slash command input | one `command_submission_id` generated before parsing |
| Command result/progress | one result identity generated when execution starts and retained on replacement |
| Durable user/assistant/direct entry | durable `entry.id` |
| Live assistant segment | `turn.id` plus deterministic assistant ordinal |
| Thinking interval | `turn.id` plus deterministic thinking ordinal |
| Tool | protocol `call_id` |
| Change summary | `change_set_id` plus owning Turn/operation context |

### Atomic Thread replacement

```ts
export interface ThreadReplacementRequest {
  readonly threadId: string;
  readonly expectedGeneration: number;
  readonly reason: "new" | "resume";
}

export type ThreadReplacementResult =
  | { readonly kind: "replaced"; readonly generation: number }
  | { readonly kind: "stale" };
```

`useThreadReplacement()` reads `application.getState` and `thread.read`, verifies the current generation and returned active Thread ID, hydrates the durable page, and dispatches exactly one `thread.replaced`. The action also carries `transcript_persisted: boolean`. For `reason: "new"`, its transcript is one session-only `StatusBlock` with key `thread-start:<thread_id>`, message `New conversation started`, and `transcript_persisted: false`; for resume, it uses hydrated blocks and `transcript_persisted: true`.

## Task 0: Prepare the PR2 Branch

**Files:**
- Create during execution: `.codex/pr-bodies/pr2-transcript-thread-replacement.md`

**Interfaces:**
- Consumes: merged PR1 Protocol v2 integration head.
- Produces: clean branch `codex/pr2-transcript-thread-replacement`.

- [ ] **Step 1: Update and branch from integration**

```powershell
git switch codex/tui-command-visual-consistency
git pull --ff-only
git status --short --branch
git switch -c codex/pr2-transcript-thread-replacement
```

Expected: clean PR2 branch based on merged PR1.

## Task 1: Add Command Input as a First-class Transcript Block

**Files:**
- Modify: `tui/src/transcript/model.ts`
- Modify: `tui/src/transcript/identity.ts`
- Modify: `tui/src/state/actions.ts`
- Modify: `tui/src/state/reducer.ts`
- Modify: `tui/src/components/transcript/blocks/BlockView.tsx`
- Create: `tui/tests/transcript/command-input.test.ts`
- Modify: `tui/tests/components/transcript.test.tsx`

**Interfaces:**
- Consumes: current Surface generation and theme user role.
- Produces: `CommandInputBlock`, `createCommandSubmissionId()`, and `transcript.command.submitted`.

- [ ] **Step 1: Write failing reducer and render tests**

```ts
store.dispatch({
  type: "transcript.command.submitted",
  submission_id: "command_11111111111111111111111111111111",
  text: "/resume thread_abcd",
  generation: 0,
});
expect(store.getState().committed_transcript).toContainEqual({
  key: "command:command_11111111111111111111111111111111",
  kind: "command_input",
  submission_id: "command_11111111111111111111111111111111",
  text: "/resume thread_abcd",
});
expect(rendered).toContain("❯ /resume thread_abcd");
```

Also assert an old-generation submission is ignored and command input has no conversation persistence status.

- [ ] **Step 2: Run focused tests and verify missing action/type**

```powershell
npm --prefix tui test -- --run tests/transcript/command-input.test.ts tests/components/transcript.test.tsx
```

Expected: FAIL because the block/action does not exist.

- [ ] **Step 3: Implement the canonical block, action, identity, and rendering**

Render `command_input` with the exact same `theme.user` color and `❯ ` prefix as normal user input, without `You`, a command title, or muted result styling.

- [ ] **Step 4: Run focused tests and commit**

```powershell
npm --prefix tui test -- --run tests/transcript/command-input.test.ts tests/components/transcript.test.tsx tests/transcript/identity.test.ts
git add tui/src/transcript tui/src/state tui/src/components/transcript tui/tests/transcript tui/tests/components/transcript.test.tsx
git commit -m "feat: record submitted slash commands"
```

Expected: PASS.

## Task 2: Submit Slash History Before Any Command Outcome

**Files:**
- Modify: `tui/src/app/App.tsx`
- Modify: `tui/src/app/use-command-execution.ts`
- Create: `tui/tests/components/app-command-flow.test.tsx`
- Modify: `tui/tests/commands/controller.test.ts`

**Interfaces:**
- Consumes: Task 1 command action and PR1 typed controller outcomes.
- Produces: one command input block for valid, invalid, local, Core, Picker, Secret, cancelled, and failed slash submissions.

- [ ] **Step 1: Write failing flow tests**

Cover these exact inputs:

```text
/status
/resume thread_deadbeef
/help
/unknown
/auth
```

Assert the command block exists before the RPC promise resolves; `/unknown` has a command block followed by a visible error; cancelling `/auth` retains `/auth`; and no command input appears in `turn.submit` or `thread.read` payloads.

- [ ] **Step 2: Run flow tests and verify delayed/missing history**

```powershell
npm --prefix tui test -- --run tests/components/app-command-flow.test.tsx tests/commands/controller.test.ts
```

Expected: FAIL because commands are currently represented only by result blocks.

- [ ] **Step 3: Implement submit ordering**

At the start of `submit(value)`, after rejecting blank input but before `parseInput(value)`, execute:

```ts
const submitted = value.trimStart();
if (submitted.startsWith("/")) {
  store.dispatch({
    type: "transcript.command.submitted",
    submission_id: createCommandSubmissionId(),
    text: submitted,
    generation: store.getState().thread_generation,
  });
}
```

Keep result blocks separate. Remove command-name titles from being treated as the submitted input.

- [ ] **Step 4: Run tests and commit**

```powershell
npm --prefix tui test -- --run tests/components/app-command-flow.test.tsx tests/commands/controller.test.ts tests/commands/local.test.ts
git add tui/src/app tui/tests/components/app-command-flow.test.tsx tui/tests/commands
git commit -m "feat: show commands before execution"
```

Expected: PASS.

## Task 3: Make Thread Replacement One Generation-guarded Operation

**Files:**
- Create: `tui/src/app/use-thread-replacement.ts`
- Modify: `tui/src/app/App.tsx`
- Modify: `tui/src/state/reducer.ts`
- Modify: `tui/src/commands/controller.ts`
- Create: `tui/tests/app/thread-replacement.test.tsx`
- Modify: `tui/tests/state/reducer.test.ts`
- Modify: `tui/tests/surface/controller.test.ts`

**Interfaces:**
- Consumes: PR1 `ThreadCommandPayload` and the canonical replacement interface.
- Produces: one atomic `thread.replaced` dispatch and stale-result rejection.

- [ ] **Step 1: Write failing new/resume/race tests**

Assert:

- `/new` response identifies the new Thread, then one replacement clears old blocks and adds only `New conversation started`;
- `/resume` replaces with hydrated selected-Thread history and no new notice;
- a delayed old `thread.read` result returns `stale` and cannot overwrite a newer generation;
- replacement clears active operation, pending interaction, warnings, usage, latest change, and old transcript;
- replacement restores Composer mode and calls the existing Thread-scoped lifecycle reset once.

- [ ] **Step 2: Run replacement tests and verify App special-case failure**

```powershell
npm --prefix tui test -- --run tests/app/thread-replacement.test.tsx tests/state/reducer.test.ts tests/surface/controller.test.ts
```

Expected: FAIL because fetching, hydration, generation checking, and lifecycle reset are currently embedded in `App.tsx`.

- [ ] **Step 3: Implement the dedicated replacement hook**

Use the canonical request/result interface. Require `application.current_thread_id === request.threadId` and `thread.view.thread.id === request.threadId`. If either differs, surface a typed command error rather than merging data. Check generation immediately before dispatch.

- [ ] **Step 4: Strengthen reducer replacement reset**

`thread.replaced` returns a freshly constructed state containing only connection, latest event sequence, incremented generation, new Application, new Thread, empty warnings, replacement transcript, and `transcript_persisted: true`. It must not spread old operational state.

- [ ] **Step 5: Delete inline replacement orchestration and commit**

```powershell
npm --prefix tui test -- --run tests/app/thread-replacement.test.tsx tests/state/reducer.test.ts tests/surface/controller.test.ts tests/commands/controller.test.ts
git add tui/src/app tui/src/state/reducer.ts tui/src/commands/controller.ts tui/tests/app tui/tests/state tui/tests/surface tui/tests/commands/controller.test.ts
git commit -m "refactor: replace thread surfaces atomically"
```

Expected: PASS.

## Task 4: Route Status Through the Transcript

**Files:**
- Modify: `tui/src/app/App.tsx`
- Modify: `tui/src/commands/presenters.ts`
- Delete: `tui/src/components/StatusCommand.tsx`
- Delete: `tui/tests/components/status-command.test.tsx`
- Modify: `tui/tests/components/app-command-flow.test.tsx`
- Modify: `tui/tests/transcript/command-results.test.ts`

**Interfaces:**
- Consumes: PR1 `StatusCommandPayload` and normal `CommandResultBlock`.
- Produces: `/status` input followed by a status result in the same transcript path.

- [ ] **Step 1: Write failing transcript Status test**

Assert no independent Status node exists and the ordered blocks are:

```ts
expect(blocks.map((block) => block.kind)).toEqual([
  "command_input",
  "command_result",
]);
```

Submitting another message must not clear Status; only Thread replacement clears it.

- [ ] **Step 2: Remove Status special state and render path**

Delete the `useState<StatusSnapshot>`, `setStatus`, direct Schema parse, `<StatusCommand>`, and submit-time Status clearing. Route Status payload through `presentCommandResult()` like every other result.

- [ ] **Step 3: Run tests and commit**

```powershell
npm --prefix tui test -- --run tests/components/app-command-flow.test.tsx tests/transcript/command-results.test.ts tests/components/transcript.test.tsx
npm --prefix tui run typecheck
git add -A tui/src/app/App.tsx tui/src/commands/presenters.ts tui/src/components/StatusCommand.tsx tui/tests/components/status-command.test.tsx tui/tests/components/app-command-flow.test.tsx tui/tests/transcript/command-results.test.ts
git commit -m "refactor: render status in transcript"
```

Expected: PASS and deleted files have no imports.

## Task 5: Enforce Stable Unique Transcript Identities

**Files:**
- Modify: `tui/src/transcript/live.ts`
- Modify: `tui/src/transcript/hydrate.ts`
- Modify: `tui/src/transcript/merge.ts`
- Modify: `tui/src/state/reducer.ts`
- Create: `tui/tests/transcript/key-invariants.test.ts`
- Modify: existing live/hydrate/reconcile tests

**Interfaces:**
- Consumes: canonical identity table.
- Produces: deterministic unique keys that survive incremental updates and reconciliation.

- [ ] **Step 1: Write the duplicate-assistant regression test**

Build one Turn timeline containing assistant → tool → assistant and assert all projected keys are unique and stable after an additional text delta. Repeat with two commands and a replaced progress result.

```ts
const keys = projection.blocks.map((block) => block.key);
expect(new Set(keys).size).toBe(keys.length);
expect(next.blocks.slice(0, -1).map((block) => block.key)).toEqual(
  projection.blocks.slice(0, -1).map((block) => block.key),
);
```

- [ ] **Step 2: Run identity tests and verify any remaining collisions**

```powershell
npm --prefix tui test -- --run tests/transcript/key-invariants.test.ts tests/transcript/live.test.ts tests/transcript/hydrate.test.ts tests/transcript/reconcile.test.ts
```

Expected: FAIL if any path still derives multiple assistant blocks from only `turn.id`.

- [ ] **Step 3: Implement deterministic ordinal identities and strict merge checks**

Create assistant IDs once in the reducer as `assistant:<turn_id>:<one-based ordinal>` and thinking IDs as `thinking:<turn_id>:<one-based ordinal>`. Projection copies these IDs directly. Durable blocks use entry IDs. `mergeTranscriptBlocks()` deduplicates only the same stable identity representing the same block; two different blocks with the same key throw `TranscriptIdentityError` in tests/development instead of silently omitting content.

- [ ] **Step 4: Run tests and commit**

```powershell
npm --prefix tui test -- --run tests/transcript tests/state/reducer.test.ts tests/components/transcript.test.tsx
git add tui/src/transcript tui/src/state/reducer.ts tui/tests/transcript tui/tests/state/reducer.test.ts tui/tests/components/transcript.test.tsx
git commit -m "fix: enforce unique transcript identities"
```

Expected: PASS without React duplicate-key warnings.

## Task 6: Verify PR2 and Document the Boundary

**Files:**
- Modify: `docs/architecture/protocol-and-ink.md`
- Modify: `docs/superpowers/plans/2026-07-13-pr2-transcript-command-semantics-and-thread-replacement.md` evidence only

**Interfaces:**
- Consumes: completed PR2 behavior.
- Produces: recorded verification and current architecture documentation.

- [ ] **Step 1: Document command and Thread surface ownership**

Document command input as non-model session history, result separation, atomic Thread generation, stale-event rejection, and stable identity sources.

- [ ] **Step 2: Run TUI quality gates**

```powershell
npm --prefix tui run format:check
npm --prefix tui run lint
npm --prefix tui run typecheck
npm --prefix tui run build
npm --prefix tui test -- --run tests/app tests/commands tests/components tests/state tests/surface tests/transcript
```

Expected: PASS.

- [ ] **Step 3: Run focused stdio flow**

```powershell
npm --prefix tui test -- --run tests/e2e/stdio-purity.test.ts
git diff --check
git status --short
```

Expected: PASS and only planned files are changed.

- [ ] **Step 4: Commit documentation**

```powershell
git add docs/architecture/protocol-and-ink.md docs/superpowers/plans/2026-07-13-pr2-transcript-command-semantics-and-thread-replacement.md
git commit -m "docs: define transcript command ownership"
```

- [ ] **Step 5: Push, PR, and merge**

Create `.codex/pr-bodies/pr2-transcript-thread-replacement.md`, then run:

```powershell
git push -u origin codex/pr2-transcript-thread-replacement
$prUrl = gh pr create --base codex/tui-command-visual-consistency --head codex/pr2-transcript-thread-replacement --title "refactor: unify command transcript lifecycle" --body-file .codex/pr-bodies/pr2-transcript-thread-replacement.md
$prNumber = gh pr view codex/pr2-transcript-thread-replacement --json number --jq .number
gh pr checks $prNumber --watch
gh pr merge $prNumber --merge --delete-branch
git switch codex/tui-command-visual-consistency
git pull --ff-only
```

Merge only after required checks pass.

## PR2 Completion Gate

- Every submitted slash command appears immediately as `❯ /...` before its outcome.
- Invalid, failed, cancelled, local, Picker, and Secret commands retain their submitted input block.
- Command inputs never enter model conversation storage.
- `/status` uses the normal transcript result path and has no independent React state.
- `/new` clears old history and displays one new-conversation notice.
- `/resume` displays only the selected Thread's durable history.
- Old-generation events, deltas, reconciliation, and outcomes cannot contaminate the active Thread.
- Every transcript block key is stable and unique; the duplicate assistant key warning cannot recur.
- No `Static`, legacy Status component, or command-title-only history path remains.
- PR2 is merged before PR3 is executed.
