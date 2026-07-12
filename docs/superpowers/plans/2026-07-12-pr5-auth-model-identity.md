# Auth and Model Identity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make credential management complete and make one Core snapshot the source of truth for model identity.

**Architecture:** `/auth` manages credentials; `/model` selects provider/model and delegates missing credentials to Auth. Core publishes one `ModelIdentitySnapshot` consumed by Welcome, status, routing, and Agent context. Secret input remains outside transcript and logs.

**Tech Stack:** Python, Pydantic, OpenAI-compatible providers, TypeScript, Zod, Ink, Pytest, Vitest.

## Global Constraints

- Official providers in this phase are DeepSeek and Kimi only.
- Secrets are stored under AWESOME_HOME, never in the workspace.
- Environment variables outrank user configuration and cannot be deleted by Awesome.
- API keys must not appear in transcript, events, errors, logs, snapshots, or Composer history.
- Do not reintroduce the App-local `CredentialFlow` removed by PR1.

---

### Task 1: Complete credential source and mutation contracts

**Files:**
- Modify: `src/awesome_agent/config/credentials.py`
- Modify: `src/awesome_agent/config/writer.py`
- Modify: `src/awesome_agent/application/provider_configuration.py`
- Modify: `src/awesome_agent/application/contracts.py`
- Modify: `tests/unit/config/test_credentials.py`
- Modify: `tests/unit/application/test_provider_configuration.py`

**Interfaces:**
- Credential action: `add | replace | delete`.
- Result reports `configured | deleted | invalid | confirm_unverified | environment_managed` without returning secret content.

- [ ] **Step 1: Write tests for add/replace/delete and env precedence**

Assert user-file delete removes only the selected key; environment-managed delete returns `environment_managed`; no result model contains an API key field.

- [ ] **Step 2: Verify RED, implement minimal mutation, verify GREEN**

Run: `uv run pytest tests/unit/config/test_credentials.py tests/unit/application/test_provider_configuration.py -q`

Expected after implementation: PASS.

### Task 2: Define the single model identity snapshot

**Files:**
- Modify: `src/awesome_agent/modeling/catalog.py`
- Modify: `src/awesome_agent/modeling/gateway.py`
- Modify: `src/awesome_agent/application/contracts.py`
- Modify: `src/awesome_agent/application/composition.py`
- Modify: `src/awesome_agent/context/builder.py`
- Modify: `tests/unit/modeling/test_gateway.py`
- Modify: `tests/integration/test_context_pipeline.py`
- Modify: `tests/unit/application/test_status.py`

**Interfaces:**

```python
class ModelIdentitySnapshot(BaseModel):
    provider: Literal["deepseek", "kimi"]
    configured_model: str
    effective_model: str
    runtime_name: Literal["Awesome Agent"]
    fallback_active: bool
    fallback_from: str | None
```

- [ ] **Step 1: Write identity consistency tests**

Assert gateway selection, application state, status, and system context contain the same effective model. Simulate fallback and assert all surfaces update to the fallback identity.

- [ ] **Step 2: Verify RED, implement one snapshot producer, verify GREEN**

Run: `uv run pytest tests/unit/modeling/test_gateway.py tests/integration/test_context_pipeline.py tests/unit/application/test_status.py -q`

Expected after implementation: PASS. Delete duplicate provider/model presentation fields replaced by the snapshot.

### Task 3: Replace Auth UI with one state-machine flow

**Files:**
- Modify: `src/awesome_agent/application/commands.py`
- Modify: `tui/src/protocol/methods.ts`
- Modify: `tui/src/commands/controller.ts`
- Modify: `tui/src/app/App.tsx`
- Modify: `tui/src/interaction/model.ts`
- Modify: `tui/src/interaction/reducer.ts`
- Modify: `tui/src/components/SecretInput.tsx`
- Modify: `tui/tests/app/provider-flow.test.tsx`
- Modify: `tui/tests/components/secret-input.test.tsx`
- Modify: `tui/tests/commands/controller.test.ts`

- [ ] **Step 1: Write complete flow tests**

Cover provider status picker, configured feedback, add, replace, delete confirmation, masked input, empty rejection, validation failure, unverified confirmation, Esc cancellation, and Composer restoration.

- [ ] **Step 2: Add secret-leak assertions**

Use a sentinel key and assert it is absent from every captured frame, store state, event, error, transcript block, and test snapshot.

- [ ] **Step 3: Verify RED**

Run: `npm --prefix tui run test -- tests/app/provider-flow.test.tsx tests/components/secret-input.test.tsx tests/commands/controller.test.ts`

Expected: delete/update and one or more feedback cases fail.

- [ ] **Step 4: Implement and delete old flow**

The interaction reducer owns Auth state; App maps actions to RPC. Delete the obsolete Auth-specific orchestration callbacks and duplicate `/model` credential path; do not reintroduce `CredentialFlow` or `commandInputBlocked` from before PR1.

- [ ] **Step 5: Run protocol and PR validation**

```bash
uv run python scripts/generate_protocol_fixtures.py
uv run ruff check src tests
uv run mypy
uv run pytest tests/unit/config tests/unit/modeling tests/unit/application/test_provider_configuration.py tests/unit/application/test_status.py tests/integration/test_context_pipeline.py tests/unit/protocol -q
npm --prefix tui run lint
npm --prefix tui run typecheck
npm --prefix tui run test -- tests/app/provider-flow.test.tsx tests/components/secret-input.test.tsx tests/commands tests/protocol
```

Expected: all commands exit 0.

- [ ] **Step 6: Commit**

```bash
git add src tests tui protocol
git commit -m "feat: unify auth and model identity"
```
