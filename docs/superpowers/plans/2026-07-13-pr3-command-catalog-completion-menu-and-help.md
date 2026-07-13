# PR3 Command Catalog, Completion, Menu, and Help Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make one exact command catalog drive parsing, canonical completion, the complete scrolling Slash menu, and readable one-command-per-row Help.

**Architecture:** Keep the command inventory and owners derived from Protocol v2, but define all user-facing command metadata once in `catalog.ts`. Search returns the complete ranked result set; terminal interaction state owns selection and a ten-row viewport; Tab inserts only canonical completion, Enter executes the selected command, Esc closes the menu, and local Help is presented from the same catalog.

**Tech Stack:** TypeScript, React, Ink, Vitest, ink-testing-library.

## Global Constraints

- PR1 and PR2 must be merged before execution.
- Branch as `codex/pr3-command-catalog-menu-help` from the latest integration head and merge back to `codex/tui-command-visual-consistency`.
- Do not add aliases, fuzzy matching, new commands, `/exit`, `/clear`, `/skill`, `/workplace`, or removed Skill shortcut commands.
- The catalog contains exactly 25 commands: 20 Application, 1 Skill, and 4 Ink.
- Each catalog record has exactly `name`, `owner`, `completion`, `usage`, `description`, and `examples`.
- `completion` is executable text such as `/resume`; `usage` is help syntax such as `/resume [thread_id]`.
- Tab inserts `completion` only and never inserts brackets or placeholders.
- Search returns every match; a viewport limits rendering to ten rows but does not truncate data.
- Up/Down wrap across the complete result set and keep the selected row visible.
- Enter executes the selected command immediately through the normal PR2 submit path; it does not merely edit Composer.
- Esc closes the menu without modifying Composer text.
- Menu rows show canonical command completion and description, not usage placeholders.
- `/help` renders one command per row with aligned usage and description; focused Help shows one command without exposing internal owner enums.
- Unknown and invalid Help requests produce explicit transcript results.
- Keep one input owner and existing key priority. Do not add a second `useInput` listener.
- No visual redesign beyond the approved Scheme A menu/help structure; PR4 owns shared result panels.

---

## Canonical Interfaces

```ts
export interface CommandMetadata {
  readonly name: CommandName;
  readonly owner: CommandOwner;
  readonly completion: `/${CommandName}`;
  readonly usage: string;
  readonly description: string;
  readonly examples: readonly string[];
}

export interface CommandMenuWindow {
  readonly items: readonly CommandMetadata[];
  readonly start: number;
  readonly end: number;
  readonly total: number;
}
```

The command menu mode is:

```ts
{
  readonly kind: "command_menu";
  readonly query: string;
  readonly selectedCommand?: CommandName;
  readonly viewportStart: number;
}
```

Viewport calculation:

```ts
export function commandMenuWindow(
  matches: readonly CommandMetadata[],
  selectedCommand: CommandName | undefined,
  viewportStart: number,
  viewportSize = 10,
): CommandMenuWindow {
  const selected = Math.max(
    0,
    matches.findIndex((item) => item.name === selectedCommand),
  );
  const maximumStart = Math.max(0, matches.length - viewportSize);
  let visibleStart = Math.max(0, Math.min(viewportStart, maximumStart));
  if (selected < visibleStart) visibleStart = selected;
  else if (selected >= visibleStart + viewportSize) {
    visibleStart = selected - viewportSize + 1;
  }
  visibleStart = Math.max(0, Math.min(visibleStart, maximumStart));
  const end = Math.min(matches.length, visibleStart + viewportSize);
  return {
    items: matches.slice(visibleStart, end),
    start: visibleStart,
    end,
    total: matches.length,
  };
}
```

## Task 0: Prepare the PR3 Branch

**Files:**
- Create during execution: `.codex/pr-bodies/pr3-command-catalog-menu-help.md`

**Interfaces:**
- Consumes: merged PR2 integration head.
- Produces: clean `codex/pr3-command-catalog-menu-help` branch.

- [ ] **Step 1: Update and branch**

```powershell
git switch codex/tui-command-visual-consistency
git pull --ff-only
git status --short --branch
git switch -c codex/pr3-command-catalog-menu-help
```

Expected: clean PR3 branch.

## Task 1: Separate Catalog Identity, Completion, Usage, and Description

**Files:**
- Modify: `tui/src/commands/catalog.ts`
- Modify: `tui/tests/commands/catalog.test.ts`
- Modify: `tui/tests/commands/parser.test.ts`

**Interfaces:**
- Consumes: Protocol v2 `commandNames` and `commandOwners`.
- Produces: exact `CommandMetadata`, `COMMAND_CATALOG`, and `findCommand()`.

- [ ] **Step 1: Write failing metadata invariants**

```ts
for (const command of COMMAND_CATALOG) {
  expect(command.completion).toBe(`/${command.name}`);
  expect(command.completion).not.toMatch(/[\[\]]/u);
  expect(command.usage).toMatch(new RegExp(`^/${command.name}(?: |$)`));
  expect(command.description.trim()).not.toBe("");
}
expect(COMMAND_CATALOG).toHaveLength(25);
```

Assert `/workspace` description says it shows the current workspace path, not trust details.

- [ ] **Step 2: Run catalog/parser tests and verify missing completion**

```powershell
npm --prefix tui test -- --run tests/commands/catalog.test.ts tests/commands/parser.test.ts
```

Expected: FAIL because `completion` does not exist.

- [ ] **Step 3: Implement the exact metadata record**

Replace tuple metadata with a `Readonly<Record<CommandName, Omit<CommandMetadata, "name" | "owner">>>`. Define completion without placeholders and keep current usage syntax. Derive inventory order from Protocol.

- [ ] **Step 4: Run tests and commit**

```powershell
npm --prefix tui test -- --run tests/commands/catalog.test.ts tests/commands/parser.test.ts
git add tui/src/commands/catalog.ts tui/tests/commands/catalog.test.ts tui/tests/commands/parser.test.ts
git commit -m "refactor: separate command metadata roles"
```

Expected: PASS.

## Task 2: Return Complete Search Results and Add a Ten-row Viewport

**Files:**
- Modify: `tui/src/commands/search.ts`
- Create: `tui/src/commands/menu-window.ts`
- Modify: `tui/src/interaction/model.ts`
- Modify: `tui/src/interaction/reducer.ts`
- Modify: `tui/src/components/CommandMenu.tsx`
- Modify: `tui/tests/commands/search.test.ts`
- Create: `tui/tests/commands/menu-window.test.ts`
- Create: `tui/tests/components/command-menu.test.tsx`
- Modify: `tui/tests/interaction/reducer.test.ts`

**Interfaces:**
- Consumes: Task 1 catalog.
- Produces: complete ranked `searchCommands()` and canonical `commandMenuWindow()`.

- [ ] **Step 1: Write failing complete-result and scrolling tests**

```ts
expect(searchCommands("")).toHaveLength(25);
let state = openCommandMenu("/");
for (let index = 0; index < 12; index += 1) {
  state = terminalUiReducer(state, { type: "mode.select", delta: 1 });
}
expect(state.mode).toMatchObject({
  kind: "command_menu",
  selectedCommand: COMMAND_CATALOG[12]?.name,
  viewportStart: 3,
});
```

Render assertions require ten command rows and footer `4–13 of 25` after the move.

- [ ] **Step 2: Run focused tests and verify truncation**

```powershell
npm --prefix tui test -- --run tests/commands/search.test.ts tests/commands/menu-window.test.ts tests/components/command-menu.test.tsx tests/interaction/reducer.test.ts
```

Expected: FAIL because search slices to ten and mode has no viewport.

- [ ] **Step 3: Remove search truncation and implement viewport state**

Delete `.slice(0, 10)` from search. Initialize `viewportStart: 0`; reset it when query changes and previous selection no longer matches; update it through `commandMenuWindow()` after every selection move.

- [ ] **Step 4: Render only the window with a range footer**

`CommandMenu` receives complete matches plus mode selection/viewport. Render `command.completion`, description, selected marker, and footer `${start + 1}–${end} of ${total}`. If no matches, render one explicit `No matching commands` row and no invalid `1–0` range.

- [ ] **Step 5: Run tests and commit**

```powershell
npm --prefix tui test -- --run tests/commands/search.test.ts tests/commands/menu-window.test.ts tests/components/command-menu.test.tsx tests/interaction/reducer.test.ts
git add tui/src/commands/search.ts tui/src/commands/menu-window.ts tui/src/interaction tui/src/components/CommandMenu.tsx tui/tests/commands tui/tests/components/command-menu.test.tsx tui/tests/interaction/reducer.test.ts
git commit -m "feat: scroll the complete command menu"
```

Expected: PASS.

## Task 3: Make Tab Complete and Enter Execute

**Files:**
- Modify: `tui/src/app/App.tsx`
- Modify: `tui/src/interaction/key-router.ts`
- Modify: `tui/tests/interaction/key-router.test.ts`
- Modify: `tui/tests/components/app-command-flow.test.tsx`

**Interfaces:**
- Consumes: selected catalog record and PR2 submit path.
- Produces: deterministic Tab, Enter, Esc, Up, and Down behavior.

- [ ] **Step 1: Write failing keyboard flow tests**

For `/res` with Resume selected:

- Tab changes Composer to `/resume`, closes the menu, and does not execute;
- Enter submits `/resume`, creates a `command_input` block, and opens the returned Thread Picker;
- Esc closes the menu and leaves `/res` unchanged;
- Down beyond row ten updates selection and viewport before Composer history.

- [ ] **Step 2: Run keyboard tests and verify usage-placeholder insertion**

```powershell
npm --prefix tui test -- --run tests/interaction/key-router.test.ts tests/components/app-command-flow.test.tsx
```

Expected: FAIL because Tab inserts `usage` and Enter only replaces Composer text.

- [ ] **Step 3: Implement canonical completion and execution**

`completeCommand()` dispatches Composer replacement with `command.completion`, then `mode.cancel`. `selectCurrent()` captures `command.completion`, closes the menu, replaces Composer, and invokes the existing submit function with that completion. Do not call submit twice through a subsequent key event.

- [ ] **Step 4: Run tests and commit**

```powershell
npm --prefix tui test -- --run tests/interaction/key-router.test.ts tests/interaction/reducer.test.ts tests/components/app-command-flow.test.tsx
git add tui/src/app/App.tsx tui/src/interaction tui/tests/interaction tui/tests/components/app-command-flow.test.tsx
git commit -m "fix: complete and execute selected commands"
```

Expected: PASS.

## Task 4: Render Help from the Same Catalog

**Files:**
- Create: `tui/src/commands/help.ts`
- Modify: `tui/src/commands/local.ts`
- Modify: `tui/src/commands/presenters.ts`
- Modify: `tui/tests/commands/local.test.ts`
- Create: `tui/tests/commands/help.test.ts`
- Modify: `tui/tests/components/local-commands.test.tsx`

**Interfaces:**
- Consumes: Task 1 catalog.
- Produces: `HelpResult` with aligned semantic rows and no owner enum.

```ts
export interface HelpRow {
  readonly usage: string;
  readonly description: string;
}

export interface HelpResult {
  readonly kind: "help";
  readonly rows: readonly HelpRow[];
}
```

- [ ] **Step 1: Write failing overview/focused Help tests**

Assert overview has 25 rows in catalog order, each usage and description is separate, no ` · ` command list exists, and focused `/help thinking` has exactly one row without `Owner:`.

- [ ] **Step 2: Run Help tests and verify one-line output**

```powershell
npm --prefix tui test -- --run tests/commands/help.test.ts tests/commands/local.test.ts tests/components/local-commands.test.tsx
```

Expected: FAIL because Help currently joins command names on one line.

- [ ] **Step 3: Implement semantic Help and presentation**

`helpOverview()` maps catalog records to `HelpRow`; focused Help uses `findCommand`. Local command results carry `HelpResult` rather than a prejoined string. The local Presenter converts rows to aligned `CommandPresentation` rows; PR4 will apply the final shared panel styling.

- [ ] **Step 4: Run tests and commit**

```powershell
npm --prefix tui test -- --run tests/commands/help.test.ts tests/commands/local.test.ts tests/components/local-commands.test.tsx
git add tui/src/commands tui/tests/commands tui/tests/components/local-commands.test.tsx
git commit -m "feat: render catalog-driven command help"
```

Expected: PASS.

## Task 5: Verify PR3 and Merge

**Files:**
- Modify: `docs/user-guide/commands.md`
- Modify: this plan for evidence only.

**Interfaces:**
- Consumes: completed PR3 command behavior.
- Produces: current command discovery documentation and verification evidence.

- [ ] **Step 1: Update command interaction documentation**

Document `/`, complete keyboard behavior, ten-row scrolling viewport, canonical Tab completion, and Help rows. Do not document removed aliases.

- [ ] **Step 2: Run quality and affected tests**

```powershell
npm --prefix tui run format:check
npm --prefix tui run lint
npm --prefix tui run typecheck
npm --prefix tui run build
npm --prefix tui test -- --run tests/commands tests/interaction tests/components/command-menu.test.tsx tests/components/local-commands.test.tsx tests/components/app-command-flow.test.tsx
git diff --check
```

Expected: PASS.

- [ ] **Step 3: Commit documentation**

```powershell
git add docs/user-guide tui docs/superpowers/plans/2026-07-13-pr3-command-catalog-completion-menu-and-help.md
git commit -m "docs: describe slash command discovery"
```

- [ ] **Step 4: Push, PR, and merge**

Create `.codex/pr-bodies/pr3-command-catalog-menu-help.md`, then:

```powershell
git push -u origin codex/pr3-command-catalog-menu-help
$prUrl = gh pr create --base codex/tui-command-visual-consistency --head codex/pr3-command-catalog-menu-help --title "feat: complete slash command discovery" --body-file .codex/pr-bodies/pr3-command-catalog-menu-help.md
$prNumber = gh pr view codex/pr3-command-catalog-menu-help --json number --jq .number
gh pr checks $prNumber --watch
gh pr merge $prNumber --merge --delete-branch
git switch codex/tui-command-visual-consistency
git pull --ff-only
```

## PR3 Completion Gate

- One catalog drives parser identity, menu, completion, and Help.
- Search returns all matches and viewport alone limits rendering to ten.
- Up/Down reaches every matching command and scrolls selection visibly.
- Tab inserts only `/command`; no bracket placeholder enters Composer.
- Enter executes the selected command once through the normal submit path.
- Esc closes the menu without changing input.
- `/help` shows one command per aligned row and focused Help shows no internal owner enum.
- All 25 commands remain discoverable and removed aliases remain absent.
- PR3 is merged before PR4 is executed.

## Execution Evidence — 2026-07-13

Implemented Tasks 0–4 and Task 5 Step 1 on
`codex/pr3-command-catalog-menu-help`.

- One 25-command catalog now owns identity, completion, usage, description,
  examples, menu search, and Help data.
- Search returns all matches; a generation-independent ten-row viewport keeps
  the wrapped selection visible.
- Tab inserts canonical completion without placeholders; Enter executes the
  selected command once through the PR2 submission path; Escape retains the
  draft.
- Help renders one semantic row per command and focused Help exposes no owner
  enum.

Quality-gate commands and their results are recorded after Task 5 Step 2;
push, GitHub checks, PR merge, and integration refresh remain Step 4.

- `npm --prefix tui run format:check` — passed, 182 files checked.
- `npm --prefix tui run lint` — passed, 182 files checked.
- `npm --prefix tui run typecheck` — passed.
- `npm --prefix tui run build` — passed.
- PR3 command, interaction, menu, Help, and command-flow suite — passed,
  80 tests in 14 files.
- `git diff --check` — passed.
