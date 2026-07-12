# Transcript, Markdown, and Streaming Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show user input immediately, reconcile it without duplication, and render stable terminal Markdown.

**Architecture:** Optimistic user blocks use a client message identity that Core echoes in Turn acceptance and persisted messages. Completed history and active projection merge by identity. Marked tokenizes completed Markdown; a bounded partial renderer handles incomplete streamed syntax without emitting HTML.

**Tech Stack:** TypeScript, React, Ink, Zod, `marked`, Python conversation/application contracts, Pytest, Vitest.

**Dependency decision:** Use the official [`marked` package](https://www.npmjs.com/package/marked) only as a Markdown lexer. Do not use its HTML output path or add an aging Ink-specific Markdown wrapper.

## Global Constraints

- Add `marked` as the only new production dependency in this redesign; consume lexer tokens and never render generated HTML.
- Raw HTML tokens are displayed as text, not interpreted.
- Streaming must not reparse and relayout the entire response for every token.
- User input must never disappear or duplicate after persistence/reconnect.
- Remove `You` and `Assistant` labels.

---

### Task 1: Add client message identity across the protocol

**Files:**
- Modify: `src/awesome_agent/application/contracts.py`
- Modify: `src/awesome_agent/application/turns.py`
- Modify: `src/awesome_agent/conversation/models.py`
- Modify: `src/awesome_agent/conversation/service.py`
- Modify: `tui/src/protocol/methods.ts`
- Modify: `tui/src/commands/controller.ts`
- Create: `tui/src/transcript/identity.ts`
- Create: `tui/tests/transcript/identity.test.ts`
- Modify: `protocol/fixtures/v1/*`
- Modify: Python/TypeScript protocol and conversation tests.

**Interfaces:**
- `turn.submit` receives `client_message_id`.
- `OperationAccepted` returns `operation_id`, `thread_id`, `turn_id`, and the same `client_message_id`.
- Persisted user message exposes the same identity.
- `createClientMessageId()` returns `client_` plus a UUID without separators; the TUI creates it before adding the optimistic block or sending RPC.

- [ ] **Step 1: Write round-trip identity tests**

Submit `client_message_id="client_1"`; assert accepted response, event identity, and `thread.read` user message all contain `client_1` exactly once.

- [ ] **Step 2: Verify RED, implement atomically, regenerate fixtures**

```bash
uv run pytest tests/unit/conversation tests/unit/application/test_turn_coordinator.py tests/unit/protocol -q
uv run python scripts/generate_protocol_fixtures.py
```

Expected after implementation: PASS; old requests without `client_message_id` are invalid.

### Task 2: Implement optimistic transcript reconciliation

**Files:**
- Modify: `tui/src/transcript/model.ts`
- Modify: `tui/src/transcript/merge.ts`
- Modify: `tui/src/transcript/reconcile.ts`
- Modify: `tui/src/state/model.ts`
- Modify: `tui/src/state/reducer.ts`
- Modify: `tui/src/app/App.tsx`
- Create: `tui/tests/transcript/merge.test.ts`
- Modify: `tui/tests/transcript/reconcile.test.ts`
- Modify: `tui/tests/surface/controller.test.ts`

**Interfaces:**
- User block lifecycle: `pending | accepted | persisted | failed`.
- Merge key is `client_message_id`, never text equality or array position.

- [ ] **Step 1: Write immediate/accepted/persisted/failure tests**

Assert the block appears before RPC resolution; persists once after reconciliation; retains text with retryable failure state after RPC failure; and is cleared by Thread replacement.

- [ ] **Step 2: Verify RED**

Run: `npm --prefix tui run test -- tests/transcript tests/surface/controller.test.ts`

Expected: immediate and deduplication cases fail.

- [ ] **Step 3: Implement one identity-based merge and delete delayed-only behavior**

Delete code paths that require final `thread.read` before creating the user block. Do not keep a text-based fallback merge.

- [ ] **Step 4: Verify GREEN**

Run: `npm --prefix tui run test -- tests/transcript tests/surface/controller.test.ts`

Expected: PASS.

### Task 3: Add terminal Markdown rendering

**Files:**
- Modify: `tui/package.json`
- Modify: `tui/package-lock.json`
- Create: `tui/src/markdown/model.ts`
- Create: `tui/src/markdown/parse.ts`
- Create: `tui/src/markdown/MarkdownBlock.tsx`
- Create: `tui/tests/markdown/parse.test.ts`
- Create: `tui/tests/markdown/render.test.tsx`
- Modify: `tui/src/components/transcript/blocks/BlockView.tsx`

**Interfaces:**
- `parseTerminalMarkdown(source: string): readonly MarkdownNode[]` uses `marked.lexer`.
- `MarkdownBlock` renders headings, paragraphs, emphasis, lists, quotes, inline code, fenced code, and links as Ink components.
- Raw HTML becomes a text node.

- [ ] **Step 1: Install the pinned parser**

Run: `npm --prefix tui install --save-exact marked@18.0.6`

Expected: `package.json` pins `marked` to exactly `18.0.6`, the lockfile resolves that version, and no other direct dependency is added.

- [ ] **Step 2: Write parser/render tests**

Use fixtures covering `#`, `**bold**`, lists, blockquotes, fenced code, links, CJK, raw HTML, malformed/incomplete fences, and narrow widths.

- [ ] **Step 3: Verify RED, implement token mapping, verify GREEN**

Run: `npm --prefix tui run test -- tests/markdown`

Expected after implementation: PASS; output does not contain raw Markdown markers for supported constructs and does not execute/interpret HTML.

### Task 4: Stabilize streaming layout and role presentation

**Files:**
- Modify: `tui/src/state/delta-batcher.ts`
- Modify: `tui/src/components/transcript/ActiveTurn.tsx`
- Modify: `tui/src/components/transcript/Transcript.tsx`
- Modify: `tui/src/components/transcript/blocks/BlockView.tsx`
- Modify: `tui/tests/transcript/live.test.ts`
- Modify: `tui/tests/components/transcript.test.tsx`

- [ ] **Step 1: Write render-count and visual-text tests**

Assert streaming batches updates, incomplete Markdown remains readable, final completion uses full Markdown, labels `You`/`Assistant` are absent, and the Composer remains after Active Turn.

- [ ] **Step 2: Implement bounded batching and stable block order**

Do not reparse completed blocks. Only the active assistant tail may update; completion replaces it by identity.

- [ ] **Step 3: Run PR validation and commit**

```bash
uv run pytest tests/unit/conversation tests/unit/application/test_turn_coordinator.py tests/unit/protocol -q
npm --prefix tui run format:check
npm --prefix tui run lint
npm --prefix tui run typecheck
npm --prefix tui run test -- tests/markdown tests/transcript tests/components/transcript.test.tsx tests/surface/controller.test.ts
git add src tests tui protocol
git commit -m "feat: render reconciled markdown transcript"
```

Expected: all commands exit 0.
