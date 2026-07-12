# Workspace Trust Experience Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Workspace Trust a focused startup gate that prevents project influence until explicit acceptance.

**Architecture:** Core remains the trust authority and SQLite store. The root TUI state machine owns startup Trust input, while `TrustPrompt` becomes a render-only view. Acceptance re-enters initialization; denial or Esc exits without creating a Thread.

**Tech Stack:** Python 3.12, SQLite, TypeScript, React, Ink, Pytest, Vitest.

## Global Constraints

- Trust is not Full access and does not alter permission mode.
- Trust state is user-level and exact-workspace-scoped; it is never written into the project.
- Before Trust, Core must not read project configuration, AGENTS.md, Skills, MCP declarations, Git branch, or project files.
- Do not add parent-directory trust inheritance, fingerprints, CLI bypass flags, or compatibility fields.

---

### Task 1: Lock the pre-trust Core boundary

**Files:**
- Modify: `tests/integration/test_workspace_trust.py`
- Modify only if the test exposes a defect: `src/awesome_agent/application/composition.py`
- Modify only if needed: `src/awesome_agent/core/workspace/service.py`

**Interfaces:**
- Consumes: `WorkspaceTrustService.status/accept`, `LocalApplication.initialize`.
- Produces: verified invariant that no project-derived loader is called before trust.

- [ ] **Step 1: Add a complete influence-barrier test**

Monkeypatch Git branch reading, project config loading, repository instructions, Skill discovery, and MCP project config discovery with call-recording fakes. Assert all call lists are empty after `initialize()` returns `TRUST_REQUIRED`, then assert each expected loader runs only after `trust` is accepted.

- [ ] **Step 2: Run and verify RED or existing compliance**

Run: `uv run pytest tests/integration/test_workspace_trust.py -q`

Expected: the new test either fails on an identified early loader or passes and records the current invariant. If it passes, do not change production Python code.

- [ ] **Step 3: Fix only an observed early-read path**

Move the observed project-derived call behind the trusted initialization branch. Do not create a second initialization path.

- [ ] **Step 4: Verify GREEN**

Run: `uv run pytest tests/integration/test_workspace_trust.py -q`

Expected: PASS.

### Task 2: Replace the Trust prompt interaction

**Files:**
- Modify: `tui/src/components/TrustPrompt.tsx`
- Modify: `tui/src/cli/main.tsx`
- Modify: `tui/src/interaction/model.ts`
- Modify: `tui/src/interaction/reducer.ts`
- Modify: `tui/src/interaction/key-router.ts`
- Modify: `tui/tests/components/trust-prompt.test.tsx`
- Modify: `tui/tests/cli/main.test.ts`
- Modify: `tui/tests/surface/startup.test.ts`

**Interfaces:**
- Adds mode: `{ kind: "workspace_trust"; workspacePath: string; selected: 0 | 1; submitting: boolean; message?: string }`.
- Produces decisions: `trust` and `deny` only.

- [ ] **Step 1: Write exact copy and keyboard tests**

Assert the frame contains `Trust this workspace?`, the canonical path, `Yes, I trust this folder`, `No, exit`, and `Enter Confirm · Esc Exit`. Assert arrows and `1`/`2` select; Enter submits; Esc sends deny.

- [ ] **Step 2: Verify RED**

Run: `npm --prefix tui run test -- tests/components/trust-prompt.test.tsx tests/cli/main.test.ts`

Expected: current Esc and copy assertions fail.

- [ ] **Step 3: Implement the render-only Trust view**

Use Mint `theme.accent` for title/current selection, a highlighted path, muted explanatory copy, and no generic `Picker`. `TrustPrompt` receives state and emits semantic actions; it must not call `useInput`.

- [ ] **Step 4: Implement submit failure recovery**

If `interaction.respond` or SQLite persistence fails, keep Trust visible, set `submitting: false`, display the product error, and do not call `beginStartup` or render Welcome.

- [ ] **Step 5: Delete replaced behavior**

Delete the old `blocking Picker` Trust branch and the test asserting Esc cannot dismiss Trust.

- [ ] **Step 6: Run PR validation**

```bash
uv run ruff check src tests
uv run mypy
uv run pytest tests/integration/test_workspace_trust.py -q
npm --prefix tui run lint
npm --prefix tui run typecheck
npm --prefix tui run test -- tests/components/trust-prompt.test.tsx tests/cli/main.test.ts tests/surface/startup.test.ts tests/interaction
```

Expected: all commands exit 0.

- [ ] **Step 7: Commit**

```bash
git add src/awesome_agent tests/integration/test_workspace_trust.py tui/src tui/tests
git commit -m "feat: redesign workspace trust gate"
```
