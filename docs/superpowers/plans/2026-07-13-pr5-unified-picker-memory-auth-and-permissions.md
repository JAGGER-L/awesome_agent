# PR5 Unified Picker, Memory, Auth, and Permissions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver one focus-safe interaction system for Trust, command Pickers, Memory, Auth, Model setup, Permissions, Approval, and secret entry with explicit configuration-source behavior and no silent state changes.

**Architecture:** Keep product decisions and mutations in Python Application services while Terminal UI state owns only the active interaction and masked input. Use one base `SelectionPanel` with neutral, brand, warning, and danger variants; move nested interaction orchestration out of `App.tsx` into a focused controller; resubmit typed command selections explicitly; and refresh Application truth after every successful credential, model, memory, or permission mutation.

**Tech Stack:** Python 3.12, Pydantic, pytest, TypeScript, React, Ink, Vitest, Protocol v2.

## Global Constraints

- PR1–PR4 must be merged before execution.
- Branch as `codex/pr5-unified-interactions` and merge to `codex/tui-command-visual-consistency`.
- Do not put secret values in command arguments, transcript blocks, logs, notices, errors, fixtures, snapshots, or project files.
- Awesome-managed credentials stay under the user Awesome home; environment variables are read-only process inputs.
- `/auth` manages DeepSeek, Kimi, and Mem0 Cloud credentials. `/memory` manages Local and Cloud Memory enablement only.
- Environment is selectable only when its variable is detected. Awesome API key remains selectable even when not configured because it opens secret entry.
- When Environment and Awesome both exist, the explicitly selected persistent source is active.
- If an explicitly selected source becomes unavailable, show `Unavailable` and require user action. Never automatically fall back to another source.
- Deleting the selected Awesome key does not silently select Environment; it leaves Awesome selected but unavailable and returns an actionable prompt to choose Environment or configure Awesome again.
- API key entry is masked, rejects empty values, submits on Enter, cancels on Esc, and restores Composer focus after terminal success/failure/cancellation.
- `/memory` first selects `Local memory` or `Cloud memory · Mem0`, then selects On/Off with the current value selected.
- Local and Cloud Memory are independent and default Off.
- Enabling Cloud Memory without a usable selected Mem0 credential returns an actionable `/auth` message and does not change Memory configuration.
- `Request approval` and `Full access` are user-facing labels; internal enums never render.
- Full access is Thread-scoped and resets on `/new`, `/resume`, and process exit.
- Tool Approval supports allow once, session edit capability, deny, and Esc cancellation using actual operation/target text from Core.
- Trust remains a startup-only blocking interaction and uses the approved safety copy; no command can run before Trust.
- Exactly one input owner handles Enter, Esc, Up, Down, and text at a time. `TerminalInput` remains the only Ink `useInput` owner.
- No compatibility interaction component or per-command key handler remains after migration.

---

## Canonical Interaction Model

```ts
export type SelectionVariant = "neutral" | "brand" | "warning" | "danger";

export type PickerOwner =
  | { readonly kind: "command"; readonly intent: CommandIntent }
  | { readonly kind: "local_theme" }
  | { readonly kind: "thread" }
  | {
      readonly kind: "credential_delete";
      readonly intent: CommandIntent;
      readonly provider: "deepseek" | "kimi" | "mem0";
    }
  | {
      readonly kind: "credential_unverified";
      readonly intent: CommandIntent;
      readonly prompt: SecretPrompt;
      readonly secret: string;
    };
```

Only the `credential_unverified` mode retains a secret briefly in process memory because the user must explicitly approve saving an unverified key. It is cleared on confirm, cancel, Thread replacement, fatal recovery, or shutdown and is never dispatched to Surface transcript state.

Input priority remains:

```text
Fatal recovery
Workspace Trust
Approval / permission escalation
Secret input
Picker
Slash menu
Composer
Global lifecycle keys
```

## Task 0: Prepare the PR5 Branch

**Files:**
- Create during execution: `.codex/pr-bodies/pr5-unified-interactions.md`

- [ ] **Step 1: Update and branch**

```powershell
git switch codex/tui-command-visual-consistency
git pull --ff-only
git status --short --branch
git switch -c codex/pr5-unified-interactions
```

Expected: clean PR5 branch.

## Task 1: Build One Selection Panel and Fixed Key Ownership

**Files:**
- Create: `tui/src/components/interactions/SelectionPanel.tsx`
- Create: `tui/src/components/interactions/SelectionRows.tsx`
- Create: `tui/src/components/interactions/index.ts`
- Modify: `tui/src/components/Picker.tsx`
- Modify: `tui/src/components/AuthPicker.tsx`
- Modify: `tui/src/components/InteractionPrompt.tsx`
- Modify: `tui/src/components/TrustPrompt.tsx`
- Modify: `tui/src/interaction/model.ts`
- Modify: `tui/src/interaction/reducer.ts`
- Modify: `tui/src/interaction/key-router.ts`
- Create: `tui/tests/components/interactions/selection-panel.test.tsx`
- Modify: existing Picker/Auth/Interaction/Trust and key-router tests

**Interfaces:**
- Consumes: typed selection options and pending Application interactions.
- Produces: one visual selection foundation with semantic variants.

- [ ] **Step 1: Write failing component and key-priority tests**

Assert neutral/base Scheme A, brand Trust, warning Full access, and danger Delete variants have border, title, selected marker, descriptions, disabled state, and `↑↓ select · Enter confirm · Esc cancel`. Disabled rows cannot be confirmed. While any modal is active, Composer does not consume Enter, Esc, Up, Down, or typed characters.

- [ ] **Step 2: Run tests and verify duplicated component behavior**

```powershell
npm --prefix tui test -- --run tests/components/picker.test.tsx tests/components/auth-picker.test.tsx tests/components/interaction-prompt.test.tsx tests/components/trust-prompt.test.tsx tests/components/interactions/selection-panel.test.tsx tests/interaction/key-router.test.ts tests/interaction/reducer.test.ts
```

Expected: FAIL until shared components and disabled-confirm behavior exist.

- [ ] **Step 3: Implement shared panel and semantic variants**

`Picker`, `AuthPicker`, `InteractionPrompt`, and `TrustPrompt` become thin domain wrappers around `SelectionPanel`. Auth may insert `Model providers` and `Memory providers` section labels, but selection rows, footer, disabled handling, border, and marker are shared.

- [ ] **Step 4: Enforce one input owner**

The key router ignores confirm while selected option is disabled or mode is submitting. Esc affects only the highest-priority mode. `mode.cancel` clears secret/unverified fields by replacing mode with Composer; it does not mutate Application state.

- [ ] **Step 5: Run tests and commit**

```powershell
npm --prefix tui test -- --run tests/components/interactions tests/components/picker.test.tsx tests/components/auth-picker.test.tsx tests/components/interaction-prompt.test.tsx tests/components/trust-prompt.test.tsx tests/interaction
git add tui/src/components tui/src/interaction tui/tests/components tui/tests/interaction
git commit -m "refactor: unify terminal selection interactions"
```

Expected: PASS.

## Task 2: Make `/memory` a Nested Layer and On/Off Picker

**Files:**
- Modify: `src/awesome_agent/application/extension_commands.py`
- Modify: `src/awesome_agent/application/composition.py`
- Modify: `tests/integration/test_local_memory.py`
- Modify: `tests/integration/test_mem0_cloud.py`
- Modify: `tui/src/commands/controller.ts`
- Create: `tui/src/app/use-interaction-controller.ts`
- Create: `tui/tests/commands/memory-flow.test.ts`

**Interfaces:**
- Consumes: PR1 `CommandSelection`, `MemoryStatusCommandPayload`, and explicit command resubmission.
- Produces: `/memory` → layer Picker → On/Off Picker → typed result.

- [ ] **Step 1: Write failing Python command grammar tests**

```python
layers = await service.memory(CommandIntent(name=CommandName.MEMORY))
assert layers.kind == "interaction"
assert [item.value for item in layers.interaction.options] == ["local", "mem0"]

local = await service.memory(
    CommandIntent(name=CommandName.MEMORY, arguments=("local",))
)
assert [item.value for item in local.interaction.options] == ["off", "on"]
assert local.interaction.options[0].selected is True
```

Add Cloud missing-credential test that returns `mem0_credential_unavailable`, mentions `/auth`, and leaves configuration Off.

- [ ] **Step 2: Run Memory tests and verify status-only behavior**

```powershell
uv run pytest tests/integration/test_local_memory.py tests/integration/test_mem0_cloud.py -q
```

Expected: FAIL because no-argument Memory currently returns status rather than a Picker.

- [ ] **Step 3: Implement exact nested semantics**

- no arguments: selection `local`, `mem0`, with context `MemoryStatusCommandPayload`;
- one argument `local` or `mem0`: selection `off`, `on`, current state selected;
- two arguments: validate and persist On/Off;
- retain explicit local list/add/replace/remove and Mem0 search/remove grammar for non-picker programmatic use, but do not expose those operations as Picker rows;
- inject `credential_statuses: Callable[[], ProviderCredentialStatuses]` into `ApplicationExtensionService` at composition;
- before enabling Mem0, require `credential_statuses().mem0.configured`, which verifies the explicitly selected source is both selected and available.

- [ ] **Step 4: Add the initial interaction controller and nested TUI test**

Create `use-interaction-controller.ts` with `openOutcome()` and `confirmSelection()` for typed command Pickers. `confirmSelection()` appends the selected value to the owner's immutable `CommandIntent`, submits it once through `CommandController.select()`, and feeds the next typed outcome back to `openOutcome()`. It does not contain credential mutation, Approval, or Secret logic until Task 4.

- [ ] **Step 5: Run tests and commit**

```powershell
npm --prefix tui test -- --run tests/commands/memory-flow.test.ts tests/commands/controller.test.ts
uv run pytest tests/integration/test_local_memory.py tests/integration/test_mem0_cloud.py -q
git add src/awesome_agent/application/extension_commands.py src/awesome_agent/application/composition.py tests/integration/test_local_memory.py tests/integration/test_mem0_cloud.py tui/src/commands/controller.ts tui/src/app/use-interaction-controller.ts tui/tests/commands/memory-flow.test.ts
git commit -m "feat: add nested memory picker"
```

Expected: PASS.

## Task 3: Finalize Auth and Model Source Semantics

**Files:**
- Modify: `src/awesome_agent/application/provider_configuration.py`
- Modify: `tests/unit/application/test_provider_configuration.py`
- Modify: `tests/unit/application/test_command_results.py`
- Modify: `tui/src/commands/controller.ts`
- Create: `tui/tests/commands/auth-flow.test.ts`
- Create: `tui/tests/commands/model-flow.test.ts`

**Interfaces:**
- Consumes: credential availability/selected source contracts and provider credential mutation RPC.
- Produces: exact Provider → Source → Action/Secret flows and refreshed truth.

- [ ] **Step 1: Write the six required Auth scenario tests**

Cover:

1. Environment only: Environment enabled/selected; Awesome selectable and opens Add secret.
2. Awesome only: Environment disabled; Awesome selected and opens Use/Replace/Delete.
3. Both: both enabled; explicit selected source marked.
4. Environment → Awesome: Use persists Awesome source.
5. Delete selected Awesome while Environment exists: Awesome remains selected but unavailable; no fallback; result directs user to select Environment or add Awesome.
6. Invalid/unverified validation: invalid preserves old secret; unverified requires explicit save-anyway confirmation.

Also cover selected Environment disappearing between launches: status shows Environment `Unavailable`, Awesome is not used automatically.

- [ ] **Step 2: Run Provider tests and verify any incomplete cases**

```powershell
uv run pytest tests/unit/application/test_provider_configuration.py tests/unit/application/test_command_results.py -q
```

Expected: FAIL on any response that hides simultaneous source availability or falls back after deletion.

- [ ] **Step 3: Implement final typed source flows**

Top `/auth` options are DeepSeek, Kimi, and Mem0. Provider selection returns Environment and Awesome API key rows with availability and selected-source state. Environment is disabled when absent. Awesome without a key returns Secret; Awesome with a key returns Use/Replace/Delete. Every successful source or credential mutation is followed by `application.getState`; the refreshed `provider_credentials` value is the only displayed truth. The mutation response remains the existing typed status/source/code contract and does not duplicate the full Application snapshot.

- [ ] **Step 4: Preserve `/model` behavior through the same setup path**

`/model` selects Provider, then if the explicitly selected credential is unavailable opens the same Add secret interaction; otherwise it opens models. It never picks another credential source automatically. After model change, refresh Application state and show the exact model ID.

- [ ] **Step 5: Run Python and TUI flow tests and commit**

```powershell
uv run pytest tests/unit/application/test_provider_configuration.py tests/unit/application/test_command_results.py -q
npm --prefix tui test -- --run tests/commands/auth-flow.test.ts tests/commands/model-flow.test.ts tests/commands/controller.test.ts
git add src/awesome_agent/application/provider_configuration.py tests/unit/application/test_provider_configuration.py tests/unit/application/test_command_results.py tui/src/commands tui/tests/commands
git commit -m "fix: make credential source selection explicit"
```

Expected: PASS and fixtures contain metadata only, never key values.

## Task 4: Move Interaction Orchestration out of `App.tsx`

**Files:**
- Modify: `tui/src/app/use-interaction-controller.ts`
- Modify: `tui/src/app/App.tsx`
- Modify: `tui/src/interaction/model.ts`
- Modify: `tui/src/interaction/reducer.ts`
- Modify: `tui/src/commands/controller.ts`
- Create: `tui/tests/app/interaction-controller.test.tsx`
- Modify: `tui/tests/components/app-command-flow.test.tsx`

**Interfaces:**
- Consumes: typed controller outcomes and terminal reducer actions.
- Produces: `openOutcome()`, `confirmSelection()`, `submitSecret()`, `cancelCurrent()`, and `respondApproval()`.

- [ ] **Step 1: Write failing controller lifecycle tests**

Assert every method:

- sets submitting state before asynchronous mutation;
- prevents double Enter;
- reports validation or RPC failure in the active panel;
- refreshes Application state only after confirmed success;
- appends visible success/error command result;
- returns to Composer on terminal completion/cancel;
- clears secret values on every terminal path;
- does not swallow Protocol failure.

- [ ] **Step 2: Run tests and verify App-owned orchestration**

```powershell
npm --prefix tui test -- --run tests/app/interaction-controller.test.tsx tests/components/app-command-flow.test.tsx
```

Expected: FAIL because credential, Picker, Secret, and Approval branches are embedded in `App.tsx`.

- [ ] **Step 3: Implement the focused hook**

The hook receives RPC/command controllers, Surface store, Terminal UI dispatch/current state, and transcript result appenders. It owns orchestration but not product state. It handles typed interactions by discriminator and never reads formatted presentation text to decide behavior.

- [ ] **Step 4: Delete replaced App branches**

Remove `mutateCredential`, credential-specific `selectCurrent` branches, Secret save logic, and Approval response orchestration from `App.tsx`. `App` delegates terminal intents to the hook and renders the active mode.

- [ ] **Step 5: Run tests and commit**

```powershell
npm --prefix tui test -- --run tests/app/interaction-controller.test.tsx tests/components/app-command-flow.test.tsx tests/interaction tests/commands
npm --prefix tui run typecheck
git add tui/src/app tui/src/interaction tui/src/commands/controller.ts tui/tests/app tui/tests/components/app-command-flow.test.tsx tui/tests/interaction tui/tests/commands
git commit -m "refactor: isolate terminal interaction orchestration"
```

Expected: PASS.

## Task 5: Complete Permission, Tool Approval, and Trust Variants

**Files:**
- Modify: `src/awesome_agent/application/permission_commands.py`
- Modify: `src/awesome_agent/application/interactions.py`
- Modify: `tests/unit/application/test_permission_commands.py`
- Modify: `tests/integration/test_headless_product.py`
- Modify: `tui/src/components/InteractionPrompt.tsx`
- Modify: `tui/src/components/TrustPrompt.tsx`
- Create: `tui/tests/app/permission-approval-flow.test.tsx`
- Modify: `tui/tests/components/interaction-prompt.test.tsx`
- Modify: `tui/tests/components/trust-prompt.test.tsx`

**Interfaces:**
- Consumes: current `PendingInteraction`, Permission session, and shared SelectionPanel.
- Produces: exact Request approval, Full access, Tool approval, and Trust behavior.

- [ ] **Step 1: Write failing permission and reset tests**

Assert `/permissions` shows Request approval and Full access; selecting Full access opens warning confirmation; confirming changes only the current Thread; `/new` and `/resume` reset Request approval; selecting Request approval immediately downgrades; cancellation changes nothing.

- [ ] **Step 2: Write Tool Approval and Trust keyboard tests**

Tool copy uses actual operation:

```text
Do you want to create circle_area.py?
Do you want to edit src/main.py?
Do you want to run `pytest tests/test_area.py`?
```

Choices cover allow once, allow ordinary file edits for this Thread where Core offers that capability, and No. Enter submits once; Esc denies/cancels and restores Composer. Trust renders the approved workspace path and safety copy, Yes/No choices, and exits on No/Esc.

- [ ] **Step 3: Run tests and implement exact variants**

```powershell
uv run pytest tests/unit/application/test_permission_commands.py tests/integration/test_headless_product.py -q
npm --prefix tui test -- --run tests/app/permission-approval-flow.test.tsx tests/components/interaction-prompt.test.tsx tests/components/trust-prompt.test.tsx tests/interaction/key-router.test.ts
```

Use neutral SelectionPanel for ordinary Pickers, warning for Full access, danger for destructive confirmation, and brand for Trust. Do not infer operation text from Tool name in TUI; render Core prompt/target.

- [ ] **Step 4: Commit permission and trust behavior**

```powershell
git add src/awesome_agent/application tests/unit/application/test_permission_commands.py tests/integration/test_headless_product.py tui/src/components tui/tests/app/permission-approval-flow.test.tsx tui/tests/components tui/tests/interaction/key-router.test.ts
git commit -m "feat: complete permission and trust interactions"
```

Expected: PASS.

## Task 6: Verify PR5, Update Docs, and Merge

**Files:**
- Modify: `docs/user-guide/configuration.md`
- Modify: `docs/user-guide/memory-skills-mcp.md`
- Modify: `docs/user-guide/workspace-and-tools.md`
- Modify: `docs/architecture/security.md`
- Modify: this plan for evidence only

- [ ] **Step 1: Update behavior documentation**

Document credential locations and precedence, explicit source selection, unavailable-source behavior, Mem0 under Auth, Memory toggles, permission modes, Tool Approval, and Trust. Do not display example secret values.

- [ ] **Step 2: Run Python gates**

```powershell
uv run ruff format --check src tests
uv run ruff check src tests
uv run mypy
uv run pytest tests/unit/application/test_provider_configuration.py tests/unit/application/test_permission_commands.py tests/unit/application/test_command_results.py tests/unit/protocol/test_contract_fixtures.py tests/integration/test_local_memory.py tests/integration/test_mem0_cloud.py tests/integration/test_headless_product.py -q
```

Expected: PASS.

- [ ] **Step 3: Run TUI gates**

```powershell
npm --prefix tui run format:check
npm --prefix tui run lint
npm --prefix tui run typecheck
npm --prefix tui run build
npm --prefix tui test -- --run tests/app tests/commands tests/components tests/interaction tests/contracts/fixtures.test.ts
git diff --check
```

Expected: PASS.

- [ ] **Step 4: Run secret and single-owner checks**

```powershell
rg -n "useInput\(" tui/src
rg -n "api_key|SecretStr|get_secret_value" tui/src tui/tests protocol/fixtures/v2
```

Expected: exactly one production `useInput` owner in `TerminalInput`; secret-related matches are schema field names or redaction assertions only, with no key values.

- [ ] **Step 5: Commit documentation**

```powershell
git add docs/user-guide docs/architecture/security.md docs/superpowers/plans/2026-07-13-pr5-unified-picker-memory-auth-and-permissions.md
git commit -m "docs: describe credentials and permissions"
```

- [ ] **Step 6: Push, PR, and merge**

Create `.codex/pr-bodies/pr5-unified-interactions.md`, then:

```powershell
git push -u origin codex/pr5-unified-interactions
$prUrl = gh pr create --base codex/tui-command-visual-consistency --head codex/pr5-unified-interactions --title "feat: unify terminal interaction flows" --body-file .codex/pr-bodies/pr5-unified-interactions.md
$prNumber = gh pr view codex/pr5-unified-interactions --json number --jq .number
gh pr checks $prNumber --watch
gh pr merge $prNumber --merge --delete-branch
git switch codex/tui-command-visual-consistency
git pull --ff-only
```

## PR5 Completion Gate

- Trust, Picker, Auth, Secret, Permission, and Approval use one input-owner system and shared visual foundation.
- `/memory` performs nested Local/Cloud then On/Off selection and preserves independent defaults.
- `/auth` exposes Environment and Awesome availability simultaneously for DeepSeek, Kimi, and Mem0.
- Explicit selected sources persist; unavailable sources warn and never silently fall back.
- API key add, replace, delete, invalid, unverified, cancellation, and refresh flows are complete without restart.
- `/model` uses the same credential setup path and shows the actual selected model.
- Full access is Thread-scoped; Request approval remains the default.
- Approval Enter/Esc/Up/Down routes correctly and Run resumes after a decision.
- Secrets never enter transcript, logs, project files, or fixtures.
- `App.tsx` no longer owns individual interaction workflows.
- PR5 is merged before PR6 is executed.

## Execution Evidence

- Replaced separate Picker, Auth, Trust, and Approval row rendering with one
  shared SelectionPanel and fixed disabled/submitting key behavior.
- Implemented nested Local/Cloud then Off/On Memory selection and blocked Mem0
  enablement when the explicitly selected credential is unavailable.
- Verified simultaneous Environment/Awesome visibility, explicit persistent
  source selection, deletion without fallback, invalid preservation, and
  unverified confirmation.
- Moved credential, Secret, Picker, and Approval orchestration out of App.tsx
  into `use-interaction-controller.ts`; the root no longer owns those flows.
- Reused the shared neutral, warning, danger, and brand variants for ordinary
  selection, Full access, destructive confirmation, and Trust.
