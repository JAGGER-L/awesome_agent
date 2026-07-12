# Visual System, Recovery, and Final Regression Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the Mint terminal design, guarantee recovery paths, remove all superseded implementation, and validate the new architecture end to end.

**Architecture:** Semantic theme tokens and stable layout components replace ad hoc colors/spacing. Recovery tests drive cancellation, rejection, auth errors, Thread switches, and Core failures back to one valid UI mode. A structural residual scan makes old input and protocol paths impossible to reintroduce.

**Tech Stack:** React, Ink, TypeScript, Vitest, Python, Pytest, NDJSON stdio E2E.

## Global Constraints

- Use the approved block `AWESOME` logo and Mint palette.
- Welcome displays the concrete model name, Thinking state, Memory state, and permission mode; it does not show `Local-first coding agent`, trust/branch suffixes, or duplicate version identities.
- Composer remains the bottom input region and is visually stronger than status text.
- Styling cannot change product semantics or hide errors.
- Final cleanup deletes superseded files/tests; no compatibility or temporary Patch remains.

---

### Task 1: Centralize semantic visual tokens and layout

**Files:**
- Modify: `tui/src/components/theme.tsx`
- Modify: `tui/src/preferences/theme.ts`
- Modify: `tui/src/components/Welcome.tsx`
- Modify: `tui/src/components/welcome-logo.ts`
- Modify: `tui/src/components/Composer.tsx`
- Modify: `tui/src/components/StatusLine.tsx`
- Modify: transcript block components.
- Modify: corresponding component and preference tests.

**Interfaces:**
- Theme roles include `brand`, `primary`, `secondary`, `muted`, `success`, `warning`, `danger`, `border`, `user`, `assistant`, `tool`.
- Layout order is `committed transcript → active turn → notices → composer → one-line status`.

- [ ] **Step 1: Write visual-contract snapshots**

Assert logo rows use the brand token, Welcome hierarchy is separated by blank space, Composer has a clear prompt/border, tool/status colors are semantic, and narrow/no-color terminals retain readable symbols.

- [ ] **Step 2: Verify RED, implement tokens/layout, verify GREEN**

Run: `npm --prefix tui run test -- tests/components/welcome.test.tsx tests/components/composer.test.tsx tests/components/status-line.test.tsx tests/components/transcript.test.tsx tests/preferences/theme.test.ts`

Expected after implementation: PASS.

### Task 2: Validate all recovery transitions

**Files:**
- Modify: `tui/src/lifecycle/cancellation.ts`
- Modify: `tui/src/lifecycle/interactions.ts`
- Modify: `tui/src/lifecycle/fatal.ts`
- Modify: `tui/src/state/reducer.ts`
- Modify: `tui/tests/lifecycle/*.test.ts`
- Modify: `tui/tests/integration/recovery.test.ts`
- Modify: `tests/e2e/test_stdio_product.py`
- Modify: `tui/tests/e2e/product-flow.test.ts`

- [ ] **Step 1: Add the recovery matrix**

Cover cancel during model streaming, cancel during tool execution, approval deny, approval RPC failure, Auth invalid/cancel, `/new` during idle, stale event after Thread switch, Core exit, and next-message submission after every nonfatal case.

- [ ] **Step 2: Verify RED**

Run: `npm --prefix tui run test -- tests/lifecycle tests/integration/recovery.test.ts tests/e2e/product-flow.test.ts`

Expected: any remaining non-recoverable mode fails explicitly.

- [ ] **Step 3: Fix only invalid transitions**

Each nonfatal terminal path must end in `{ kind: "composer" }`; fatal paths render FatalScreen and disable input. Do not add timers that hide unresolved Core state.

- [ ] **Step 4: Verify GREEN**

Run the same command and `uv run pytest tests/e2e/test_stdio_product.py -q`.

Expected: PASS.

### Task 3: Delete residual architecture and enforce boundaries

**Files:**
- Modify: `tests/structural/test_product_architecture.py`
- Modify: `tests/structural/test_application_architecture.py`
- Modify: `tui/tests/structural/ink-boundaries.test.ts`
- Delete: every superseded component, old protocol fixture, state field, compatibility parser, and implementation-coupled test identified by the scan.

- [ ] **Step 1: Add structural assertions**

Assert:

```text
only TerminalInput.tsx imports useInput
App.tsx has no commandInputBlocked/CredentialFlow/modal boolean cluster
no execute_boundary interaction kind remains
no choices: string[] interaction contract remains
no You › or Assistant › label remains
no Help overlay remains
no live duration_ms: 0 fallback remains
no protocol version branch or alias accepts replaced fields
```

- [ ] **Step 2: Run the structural tests and delete every discovered residual**

```bash
uv run pytest tests/structural -q
npm --prefix tui run test -- tests/structural
```

Expected: PASS only after old paths are physically removed.

### Task 4: Update user and architecture documentation

**Files:**
- Modify: `README.md`
- Modify: `README.zh-CN.md`
- Modify: `docs/getting-started/quickstart.md`
- Modify: `docs/getting-started/quickstart.zh-CN.md`
- Modify: `docs/user-guide/commands.md`
- Modify: `docs/user-guide/workspace-and-tools.md`
- Modify: `docs/architecture/security.md`
- Modify: `docs/architecture/protocol-and-ink.md`
- Modify: `ARCHITECTURE.md`

- [ ] **Step 1: Document only shipped behavior**

Describe Trust, `/permissions`, Request approval, Full access warning/reset, `/auth`, `/model`, Slash keyboard behavior, Markdown, tool timeline, cancellation, and exact hard-deny boundary. Keep English/Chinese entry docs aligned.

- [ ] **Step 2: Validate links and terminology**

Run: `uv run pytest tests/structural/test_markdown_links.py -q`

Expected: PASS; search finds no removed command or interaction terminology.

### Task 5: Run the new architecture's full release gate

- [ ] **Step 1: Python quality gates**

```bash
uv run ruff format --check src tests
uv run ruff check src tests
uv run mypy
uv run pytest -m "not external" -q
```

Expected: all current local new-architecture tests pass; external credential/network tests are explicitly excluded unless credentials are intentionally supplied in a separate run.

- [ ] **Step 2: TUI quality gates**

```bash
npm --prefix tui run format:check
npm --prefix tui run lint
npm --prefix tui run typecheck
npm --prefix tui run test
npm --prefix tui run build
```

Expected: all commands exit 0.

- [ ] **Step 3: Product smoke**

Run the packaged `awesome` in a fresh untrusted workspace and manually verify Trust, `/auth`, `/model`, one read task, one create-only task, Approval, Full access, `/new`, `/resume`, Markdown, cancellation, and clean exit. Record observed results in the PR description; do not store credentials or raw secrets.

- [ ] **Step 4: Inspect final diff and commit**

```bash
git status --short
git diff --check
git diff --name-status
git add README.md README.zh-CN.md ARCHITECTURE.md docs src tests tui protocol
git commit -m "feat: complete terminal interaction redesign"
```

Expected: no temporary files, secrets, compatibility adapters, unrelated changes, or unexplained untracked files.
