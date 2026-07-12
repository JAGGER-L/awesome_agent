# Permission and Approval Policy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish one Core capability policy with Request approval and thread-scoped Full access modes.

**Architecture:** Tools declare capabilities. A single policy engine returns ALLOW, ASK, or DENY. Permission mode can convert ASK to ALLOW but cannot bypass DENY. Structured Interaction payloads cross the protocol and the TUI only renders/responds.

**Tech Stack:** Python 3.12, Pydantic, TypeScript, Zod, Ink, Pytest, Vitest.

## Global Constraints

- Request approval is the default for every new/resumed Thread and process launch.
- Full access is thread-scoped, non-persistent, and resets on `/new`, `/resume`, and exit.
- Full access cannot bypass workspace path policy, sensitive-path policy, schema validation, budgets, timeout, cancellation, privilege-elevation deny, shutdown deny, disk-operation deny, or root-deletion deny.
- `workspace.write` session grants do not cover `workspace.delete` or `shell.execute`.
- Do not keep `command_policy.InteractionRequired` as a parallel approval path.

---

### Task 1: Define capability and permission domain models

**Files:**
- Create: `src/awesome_agent/core/tools/permissions.py`
- Modify: `src/awesome_agent/core/tools/contracts.py`
- Modify: `src/awesome_agent/core/tools/registry.py`
- Create: `tests/unit/core/tools/test_permissions.py`
- Modify: `tests/structural/test_tool_architecture.py`

**Interfaces:**

```python
class ToolCapability(StrEnum):
    WORKSPACE_READ = "workspace.read"
    WORKSPACE_WRITE = "workspace.write"
    WORKSPACE_DELETE = "workspace.delete"
    SHELL_EXECUTE = "shell.execute"

class PermissionMode(StrEnum):
    REQUEST_APPROVAL = "request_approval"
    FULL_ACCESS = "full_access"

class PolicyAction(StrEnum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"
```

- [ ] **Step 1: Write policy-table tests**

Cover read/write/delete/shell in both modes, thread write grant, unknown extension capability, sensitive path, and hard-deny command.

- [ ] **Step 2: Verify RED**

Run: `uv run pytest tests/unit/core/tools/test_permissions.py -q`

Expected: FAIL because the domain does not exist.

- [ ] **Step 3: Implement immutable policy inputs/results**

`PermissionPolicy.evaluate(request) -> PolicyDecision` must be pure. Register each built-in with one capability; extension tools must declare a capability before registration.

- [ ] **Step 4: Verify GREEN**

Run: `uv run pytest tests/unit/core/tools/test_permissions.py tests/structural/test_tool_architecture.py -q`

Expected: PASS.

### Task 2: Replace Interaction contracts and executor branching

**Files:**
- Modify: `src/awesome_agent/application/interactions.py`
- Modify: `src/awesome_agent/application/contracts.py`
- Modify: `src/awesome_agent/application/composition.py`
- Modify: `src/awesome_agent/core/tools/context.py`
- Modify: `src/awesome_agent/core/tools/executor.py`
- Delete: approval control-flow use from `src/awesome_agent/core/tools/command_policy.py`
- Modify: `tests/unit/application/test_interactions.py`
- Modify: `tests/unit/core/tools/test_registry_executor.py`
- Modify: `tests/unit/core/tools/test_execute.py`
- Modify: `tests/unit/core/tools/test_modifying_tools.py`

**Interfaces:**

```python
class InteractionKind(StrEnum):
    WORKSPACE_TRUST = "workspace_trust"
    TOOL_APPROVAL = "tool_approval"
    FULL_ACCESS_CONFIRMATION = "full_access_confirmation"

class InteractionDecision(StrEnum):
    TRUST = "trust"
    ALLOW_ONCE = "allow_once"
    ALLOW_THREAD_WRITES = "allow_thread_writes"
    ENABLE_FULL_ACCESS = "enable_full_access"
    DENY = "deny"
```

`PendingInteraction` gains structured `operation`, `target`, `capability`, and ordered choice objects with labels. Remove UI dependence on raw decision strings.

- [ ] **Step 1: Write failing structured-interaction tests**

Assert a create request produces target `circle_area.py` and choices `allow_once`, `allow_thread_writes`, `deny`; delete and execute omit the thread-write choice.

- [ ] **Step 2: Verify RED**

Run: `uv run pytest tests/unit/application/test_interactions.py tests/unit/core/tools/test_registry_executor.py -q`

Expected: new contract assertions fail.

- [ ] **Step 3: Make ToolExecutor the only policy enforcement point**

Evaluate policy after schema normalization and before handler execution. For ASK, await `InteractionCoordinator`; for DENY, return normalized `PERMISSION_DENIED`; for ALLOW, execute once. Remove the `InteractionRequired` retry loop and `allowed_interaction_scopes` workaround.

- [ ] **Step 4: Verify GREEN**

Run: `uv run pytest tests/unit/application/test_interactions.py tests/unit/core/tools/test_registry_executor.py tests/unit/core/tools/test_execute.py tests/unit/core/tools/test_modifying_tools.py -q`

Expected: PASS.

### Task 3: Add thread-scoped mode and `/permissions`

**Files:**
- Modify: `src/awesome_agent/application/commands.py`
- Modify: `src/awesome_agent/application/dispatcher.py`
- Modify: `src/awesome_agent/application/contracts.py`
- Modify: `src/awesome_agent/application/composition.py`
- Modify: `tests/unit/application/test_command_contracts.py`
- Modify: `tests/unit/application/test_dispatcher.py`
- Modify: `tests/integration/test_agent_turn.py`

**Interfaces:**
- Adds `CommandName.PERMISSIONS`.
- Application state/status exposes `permission_mode`.
- The foreground Application owns one non-persistent `PermissionSession(mode, grants)` for the active Thread and replaces it on every new/resume switch; it is not stored in the Thread repository.

- [ ] **Step 1: Write mode lifecycle tests**

Assert default Request approval; enable Full access only after confirmation; reject switching during active operation/pending interaction; clear mode and grants on new/resume.

- [ ] **Step 2: Verify RED, implement, and verify GREEN**

Run RED/GREEN command: `uv run pytest tests/unit/application/test_command_contracts.py tests/unit/application/test_dispatcher.py tests/integration/test_agent_turn.py -q`

Expected after implementation: PASS.

### Task 4: Upgrade protocol and Approval UI atomically

**Files:**
- Modify: `src/awesome_agent/core/events.py`
- Modify: `src/awesome_agent/protocol/jsonrpc.py`
- Modify: `tui/src/protocol/events.ts`
- Modify: `tui/src/protocol/methods.ts`
- Modify: `tui/src/state/model.ts`
- Modify: `tui/src/state/reducer.ts`
- Modify: `tui/src/components/InteractionPrompt.tsx`
- Modify: `tui/src/components/StatusCommand.tsx`
- Modify: `tui/src/components/StatusLine.tsx`
- Modify: `tui/src/commands/catalog.ts`
- Modify: `protocol/fixtures/v1/*`
- Test: Python and TypeScript protocol contract suites plus interaction component suites.

- [ ] **Step 1: Change both schema tests to the new payload**

The TUI projection must contain typed choices `{ decision, label, description? }`, capability, operation, target, and interaction identity.

- [ ] **Step 2: Regenerate fixtures and remove old payload acceptance**

Run: `uv run python scripts/generate_protocol_fixtures.py`

Delete tests and schemas accepting `execute_boundary` or `choices: string[]`. Do not version-branch parsers.

- [ ] **Step 3: Render and route Approval/Full access**

`/permissions` opens a picker. Switching to Full access opens the warning confirmation. Approval Enter/Esc uses the root router; responding/failure is visible; resolved restores the previous flow.

- [ ] **Step 4: Run PR validation**

```bash
uv run ruff check src tests
uv run mypy
uv run pytest tests/unit/application tests/unit/core/tools tests/integration/test_agent_turn.py tests/unit/protocol -q
npm --prefix tui run lint
npm --prefix tui run typecheck
npm --prefix tui run test -- tests/protocol tests/components/interaction-prompt.test.tsx tests/lifecycle/interactions.test.ts tests/app
```

Expected: all commands exit 0.

- [ ] **Step 5: Commit**

```bash
git add src tests tui protocol
git commit -m "feat: unify permission and approval policy"
```
