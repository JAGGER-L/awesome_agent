# Tool Timeline and Agent Stop Behavior Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render an honest Thinking/Tool/Answer timeline and stop the Agent when the user's goal is complete.

**Architecture:** Core events carry measured durations and structured tool presentation facts. The TUI projects them in event order and owns only folding. Agent context defines minimal action; graph routing continues only when the model actually requested tools and always feeds results back.

**Tech Stack:** Python Agent Core, LangGraph, Pydantic events, TypeScript, Ink, Pytest, Vitest.

## Global Constraints

- Never label local waiting time as Provider-internal reasoning time.
- Tool duration comes from Core monotonic measurement; Turn duration comes from Turn start/terminal events.
- Long output is folded only for display and remains available in runtime/audit state according to existing bounds.
- A successful file write must not automatically trigger Shell.
- Do not solve Agent over-execution only with a TUI message or post-hoc suppression.

---

### Task 1: Enrich tool and turn events with presentation facts

**Files:**
- Modify: `src/awesome_agent/core/events.py`
- Modify: `src/awesome_agent/core/tools/contracts.py`
- Modify: `src/awesome_agent/core/tools/executor.py`
- Modify: built-in tools under `src/awesome_agent/core/tools/builtins/`
- Modify: `src/awesome_agent/application/events.py`
- Modify: `tests/unit/core/test_events.py`
- Modify: `tests/unit/core/tools/test_registry_executor.py`

**Interfaces:**

```python
class ToolPresentation(BaseModel):
    verb: str
    target: str | None
    outcome: str | None
    summary: str
    detail: str | None
    duration_ms: int | None
```

Tool started includes verb/target; terminal includes outcome/summary/detail/duration. Turn terminal includes `duration_ms`.

- [ ] **Step 1: Write exact event tests**

Assert write emits `Write`, path target, `Created`/`Updated`, line count, and measured duration; execute emits command target and exit summary; failed tools include normalized error without secret arguments.

- [ ] **Step 2: Verify RED, implement in Core, verify GREEN**

Run: `uv run pytest tests/unit/core/test_events.py tests/unit/core/tools/test_registry_executor.py tests/unit/core/tools/test_modifying_tools.py tests/unit/core/tools/test_execute.py -q`

Expected after implementation: PASS.

### Task 2: Upgrade protocol and ordered timeline projection

**Files:**
- Modify: `tui/src/protocol/events.ts`
- Modify: `tui/src/state/model.ts`
- Modify: `tui/src/state/reducer.ts`
- Modify: `tui/src/transcript/model.ts`
- Modify: `tui/src/transcript/live.ts`
- Modify: `tui/src/components/transcript/ActiveTurn.tsx`
- Modify: `tui/src/components/transcript/blocks/BlockView.tsx`
- Modify: `protocol/fixtures/v1/*`
- Modify: `tui/tests/transcript/live.test.ts`
- Modify: `tui/tests/components/transcript.test.tsx`

**Interfaces:**
- Active Turn stores an ordered timeline union: `thinking | tool | assistant`.
- TUI records local thinking-state start/end from event transitions and labels it `Thought for ...`; it does not receive a fake reasoning duration.

- [ ] **Step 1: Write ordering and duration tests**

Feed events `turn.started → model wait → tool.started → tool.completed → model wait → text → turn.completed`; assert the exact rendered order and independent durations.

- [ ] **Step 2: Verify RED, update schemas/fixtures, implement projection**

Run: `uv run python scripts/generate_protocol_fixtures.py`

Delete generic `Tool execution completed.` UI projection and live `duration_ms: 0` fallback.

- [ ] **Step 3: Add `Ctrl+O` display folding**

Use the root key router. One presentation boolean expands/collapses bounded tool details; Tool blocks do not register input listeners.

- [ ] **Step 4: Verify GREEN**

Run: `npm --prefix tui run test -- tests/protocol tests/transcript/live.test.ts tests/components/transcript.test.tsx tests/interaction`

Expected: PASS.

### Task 3: Enforce minimal action in Agent Core

**Files:**
- Modify: `src/awesome_agent/context/builder.py`
- Modify only if an observed defect exists: `src/awesome_agent/agent/nodes.py`
- Modify: `tests/integration/test_agent_turn.py`
- Modify: `tests/unit/agent/test_graph.py`
- Modify: `tests/unit/agent/test_context_routing.py`

**Interfaces:**
- System instruction states that verification requires explicit user intent, acceptance criteria, or necessity to determine completion.
- Graph routing executes only model-returned tool calls and returns every ToolResultMessage before another model call.

- [ ] **Step 1: Add behavioral regression providers**

Use deterministic fake providers for: create-one-file then final answer; create-and-test then execute; tool failure then corrected tool call; completed goal with no extra call.

- [ ] **Step 2: Verify RED or locate the actual cause**

Run: `uv run pytest tests/integration/test_agent_turn.py tests/unit/agent/test_graph.py tests/unit/agent/test_context_routing.py -q`

Expected: the create-only scenario must expose either prompt-driven extra execution or a routing defect. Record which invariant fails before changing production code.

- [ ] **Step 3: Fix the responsible layer only**

If the graph already stops correctly, change only system context. If routing fabricates/replays calls or omits tool results, fix that invariant and add its unit regression. Do not add a tool-name blacklist or suppress valid execute calls.

- [ ] **Step 4: Run PR validation and commit**

```bash
uv run ruff check src tests
uv run mypy
uv run pytest tests/unit/core tests/unit/agent tests/integration/test_agent_turn.py tests/unit/protocol -q
npm --prefix tui run lint
npm --prefix tui run typecheck
npm --prefix tui run test -- tests/protocol tests/transcript tests/components/transcript.test.tsx tests/interaction
git add src tests tui protocol
git commit -m "feat: add honest tool timeline and minimal action"
```
Expected: all commands exit 0.
