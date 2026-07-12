# Slash Commands and Thread Switching Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn Slash Commands into a complete keyboard workflow and make `/new` replace all old Thread projections.

**Architecture:** Parsing/catalog remain pure. The unified UI mode owns candidate selection and Tab completion. Command results become transcript status blocks. Thread switching uses a generation identity so stale async results cannot mutate the new view.

**Tech Stack:** TypeScript, React, Ink, Python application commands, Pytest, Vitest.

## Global Constraints

- `/help` is transcript content, not a blocking overlay.
- `/usage` always gives visible feedback, including when no usage exists.
- `/new` clears transcript, active turn, pending optimistic input, temporary permission, cancellation, and stale request effects.
- Keep `/editor` and `/details` deleted.
- Do not keep the read-only CommandMenu next to the interactive menu.

---

### Task 1: Build selectable command-menu state

**Files:**
- Modify: `tui/src/commands/search.ts`
- Modify: `tui/src/commands/controller.ts`
- Modify: `tui/src/components/CommandMenu.tsx`
- Modify: `tui/src/interaction/model.ts`
- Modify: `tui/src/interaction/reducer.ts`
- Modify: `tui/src/interaction/key-router.ts`
- Modify: `tui/tests/commands/search.test.ts`
- Modify: `tui/tests/commands/controller.test.ts`
- Modify: `tui/tests/components/composer.test.tsx`

**Interfaces:**
- `searchCommands(query)` returns ordered command entries.
- `command_menu` stores query plus selected command name, not a fragile array index.
- Tab dispatches `composer.replace` with command usage; Enter dispatches one command intent.

- [ ] **Step 1: Write failing keyboard-flow tests**

Test `/` opens candidates, arrows move selection, Tab completes without execution, Enter executes once, Esc closes while keeping text, and an unmatched command produces an error block.

- [ ] **Step 2: Verify RED**

Run: `npm --prefix tui run test -- tests/commands tests/components/composer.test.tsx`

Expected: current display-only menu fails selection/completion cases.

- [ ] **Step 3: Implement and remove the old menu behavior**

Render `CommandMenu` from reducer state; delete its direct query/search ownership. Composer must not use Up/Down for history while the command menu is open.

- [ ] **Step 4: Verify GREEN**

Run: `npm --prefix tui run test -- tests/commands tests/components/composer.test.tsx tests/interaction`

Expected: PASS.

### Task 2: Make command feedback part of the transcript

**Files:**
- Modify: `tui/src/transcript/model.ts`
- Modify: `tui/src/transcript/merge.ts`
- Modify: `tui/src/components/transcript/blocks/BlockView.tsx`
- Modify: `tui/src/commands/local.ts`
- Modify: `tui/src/app/App.tsx`
- Delete: `tui/src/components/Help.tsx`
- Delete: `tui/tests/components/help.test.tsx`
- Create: `tui/tests/transcript/command-results.test.ts`
- Modify: `tui/tests/components/local-commands.test.tsx`

**Interfaces:**
- Adds local block `{ kind: "command_result"; command; tone; content }`.
- `help`, `usage`, notices, and command errors append blocks and immediately restore composer mode.

- [ ] **Step 1: Write tests for visible `/help` and `/usage` results**

Assert the Composer remains visible after each command and no Esc is required.

- [ ] **Step 2: Verify RED, implement, delete Help overlay, verify GREEN**

Run: `npm --prefix tui run test -- tests/components/local-commands.test.tsx tests/transcript/command-results.test.ts`

Expected after implementation: PASS and `rg -n "Help" tui/src/app tui/src/components` has no obsolete overlay reference.

### Task 3: Make `/new` an atomic Thread projection switch

**Files:**
- Modify: `tui/src/state/actions.ts`
- Modify: `tui/src/state/reducer.ts`
- Modify: `tui/src/surface/controller.ts`
- Modify: `tui/src/commands/controller.ts`
- Modify: `tui/tests/state/reducer.test.ts`
- Modify: `tui/tests/surface/controller.test.ts`
- Modify: `tui/tests/integration/recovery.test.ts`
- Modify: `tests/unit/application/test_dispatcher.py`

**Interfaces:**
- Adds atomic action `thread.replaced` with new application/thread and a monotonically increasing `thread_generation`.
- Async reconciliation/events carry the captured generation and are ignored when it no longer matches.

- [ ] **Step 1: Write stale-result and reset tests**

Start an old reconciliation promise, execute `/new`, resolve the old promise, and assert no old block reappears. Assert permission snapshot, pending interaction, active operation, warnings, change, usage, and cancellation are reset.

- [ ] **Step 2: Verify RED**

Run: `npm --prefix tui run test -- tests/state/reducer.test.ts tests/surface/controller.test.ts tests/integration/recovery.test.ts`

Expected: current hydration leaves at least one old projection or accepts a stale completion.

- [ ] **Step 3: Implement one atomic replacement**

Do not sequence separate hydrate actions that expose mixed old/new state. Remove old clear-revision workarounds that existed only to compensate for partial Thread replacement.

- [ ] **Step 4: Run PR validation and commit**

```bash
uv run pytest tests/unit/application/test_dispatcher.py -q
npm --prefix tui run lint
npm --prefix tui run typecheck
npm --prefix tui run test -- tests/commands tests/components tests/state tests/surface tests/integration/recovery.test.ts
git add src tests tui
git commit -m "feat: complete slash command and thread flows"
```

Expected: tests pass and the commit contains no Help overlay or read-only command menu path.
