# PR8 Welcome, Documentation, and Full Interaction Regression Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the approved Scheme A terminal experience, make user and developer documentation describe the shipped behavior, and prove the complete PR1-PR8 interaction architecture through focused cross-component regression.

**Architecture:** PR8 introduces no new product-semantic owner. Python Core remains authoritative for commands and product state; Protocol v2 transports exact typed outcomes; Ink presenters and shared components own terminal layout. Welcome consumes repository Logo and theme constants and computes its left panel from terminal display width rather than flex allocation or HTML measurements. Documentation is derived from the final command catalog and actual developer launcher. Regression tests cross the existing boundaries without creating a second command registry, UI state store, compatibility path, or test-only production behavior.

**Tech Stack:** Python 3.12, Protocol v2 JSON-RPC/NDJSON, TypeScript, React, Ink, Vitest, pytest, `uv`, npm.

## Global Constraints

- PR1-PR7 must be merged into `codex/tui-command-visual-consistency` before execution.
- Branch as `codex/pr8-welcome-docs-regression` from the latest integration head and merge only to that integration branch.
- Do not redefine interfaces owned by PR1-PR7. If an upstream acceptance invariant is missing, stop and correct the owning PR rather than patching around it in PR8.
- Implement the approved Welcome Scheme A. HTML prototypes communicate hierarchy and semantics only; they are not pixel, spacing, color, or width contracts for Ink.
- Import the repository Logo rows and theme tokens. Do not duplicate, regenerate, scale, trim, recolor locally, or maintain a second Logo fixture.
- Preserve the approved dark Aurora palette already held by the theme authority:
  Logo rows `#D0F5E7`, `#B0EADF`, `#95DCDA`, `#8BC9E5`, `#96B5DF`;
  brand `#A9EADC`; primary `#9BE4D6`; secondary `#8FC8E8`; border
  `#5EA9AA`; user `#B8EADF`; Tool `#88C4E2`. Components consume semantic
  roles and never embed these values locally.
- Compute layout from terminal display width, including wide Unicode behavior. Do not use JavaScript string length as terminal width.
- The wide Welcome layout uses an intrinsic-width Logo panel and a flexible details panel. The Logo must not sit inside an expanding blank left panel.
- At narrower supported widths, use the existing responsive stacked/compact presentation rather than clipping metadata or inventing horizontal scrolling.
- Welcome details remain: Version, Workspace, Thread, Model, Thinking, Local memory, Cloud memory, external memory Provider, and Permissions. Do not restore a tagline, Git branch, or trust suffix.
- The external memory label is `Cloud memory`; its Provider value is `Mem0 Cloud`. Local and Cloud memory activation are shown independently.
- Do not change Markdown semantics in this PR unless a PR8 regression proves PR1-PR7 broke the already-supported table/formula path. Preserve readable inline and block formulas, including `S = πr²`; do not build a terminal KaTeX renderer.
- The developer command remains `uv run awesome-dev`. Development data defaults to the ignored repository-local `.awesome-dev/home`, logs to `.awesome-dev/logs`, and the target workspace receives no Awesome state.
- Keep `README.md` and `README.zh-CN.md` behaviorally consistent. Keep English and Chinese Quickstarts behaviorally consistent and user-friendly rather than mechanically line-for-line identical.
- Remove only tests/components/snapshots proven obsolete because PR1-PR8 replaced their behavior. Do not perform broad cache or repository cleanup in this PR.
- Do not add skips, expected failures, permissive Protocol schemas, swallowed exceptions, compatibility aliases, or hidden fallbacks to make regression pass.
- Network/live-Provider tests, installer tests on other hosts, release publication, and merging the integration branch to `main` remain outside this PR.

---

## Final Ownership Check

Before editing, record the following authority map in the PR notes and reject any implementation that violates it:

| Concern | Single owner | PR8 may do |
| --- | --- | --- |
| Command names, ownership, completion, help metadata | PR3 command catalog generated from Protocol command names | Audit and render; never create another list |
| Command semantics and payloads | Python Application command dispatcher from PR1 | Exercise contracts; never infer missing semantics in Ink |
| Command terminal presentation | PR4 Presenter registry and shared result components | Verify every outcome and refine only shared visual tokens |
| Thread transcript replacement | PR2 Surface reducer and transcript generation | Exercise `/new` and `/resume`; never clear scrollback ad hoc |
| Modal input ownership | PR5 interaction orchestrator and terminal input router | Exercise the keyboard matrix; never add component-local listeners |
| Change lifecycle | PR6 command lifecycle/presenters | Exercise Compact/Diff/Undo/Redo states |
| Thinking, Tool sequence, details, Worked | PR7 activity projection and global detail mode | Exercise live/folded/resumed behavior |
| Welcome layout | `Welcome.tsx` using Logo/theme/display-width utilities | Implement Scheme A only |
| Developer startup | `awesome_agent.development.launcher` | Verify and document the existing one-command path |

## Task 0: Prepare the PR8 Branch and Baseline

**Files:**
- Create during execution: `.codex/pr-bodies/pr8-welcome-docs-regression.md`
- Read: `.codex/exec-plans/active/` when present
- Read: all PR1-PR7 implementation plans and their merged diffs

- [ ] **Step 1: Update and branch**

```powershell
git switch codex/tui-command-visual-consistency
git pull --ff-only
git status --short --branch
git switch -c codex/pr8-welcome-docs-regression
```

Expected: a clean PR8 branch containing all seven prior PRs.

- [ ] **Step 2: Record the focused baseline before changes**

```powershell
uv run pytest tests/unit/development/test_launcher.py tests/structural/test_markdown_links.py -q
npm --prefix tui test -- --run tests/components/welcome.test.tsx tests/markdown tests/commands tests/composer tests/app tests/components tests/transcript
```

Record failures exactly. Do not normalize an existing failure into the PR8 scope without tracing it to an acceptance requirement below.

## Task 1: Implement Welcome Scheme A from Terminal Width

**Files:**
- Modify: `tui/src/components/Welcome.tsx`
- Modify only if a reusable calculation belongs there: `tui/src/layout/width.ts`
- Verify, do not duplicate: `tui/src/components/welcome-logo.ts`
- Verify, do not duplicate: `tui/src/preferences/theme.ts`
- Modify: `tui/tests/components/welcome.test.tsx`
- Add if calculation is extracted: `tui/tests/layout/width.test.ts`

**Interfaces:**
- Consumes: `FULL_LOGO_ROWS`, `COMPACT_LOGO_ROWS`, `Theme.logoRows`, `Theme.border`, Welcome product state.
- Produces: responsive Scheme A layout only.
- Does not produce: new product state, command state, or copied Logo/color data.

- [ ] **Step 1: Replace fragile render assertions with approved layout invariants**

Write failing tests for terminal widths 80, 100, and 120 that assert:

- every repository Logo row appears exactly once;
- the wide left panel's inner display width equals the maximum Logo row display width, plus only the approved horizontal padding;
- the left panel does not grow when terminal width changes from 100 to 120;
- the right details panel receives the added width;
- the Logo never wraps, truncates, or gains a blank expanding region;
- labels remain in the approved order;
- Version is one unqualified version number;
- Workspace contains only the path;
- Thread, actual Provider/model identity, Thinking, Local memory, Cloud memory, `Mem0 Cloud`, and human-readable Permissions are visible;
- `Local coding session ready`, `Local-first coding agent`, trust status, and Git branch are absent;
- no color capability changes glyph content or structure.

Test behavior and measured display cells, not a brittle full-frame snapshot.

- [ ] **Step 2: Prove the current flex allocation fails the invariant**

```powershell
npm --prefix tui test -- --run tests/components/welcome.test.tsx
```

Expected before implementation: the intrinsic-width assertion fails because the left panel has `flexGrow={1}`.

- [ ] **Step 3: Implement the intrinsic Logo panel**

Compute the widest selected Logo row with the repository's terminal display-width utility. Set the left panel width from that measurement plus Ink border/padding cells. Remove `flexGrow` from the Logo panel. Give remaining horizontal space to the details panel.

For widths where two panels cannot satisfy both minimums, stack the Logo and details panels. Continue using the compact Logo only at the established compact breakpoint; do not squeeze or scale the full Logo.

Use the existing Logo row colors and border tokens. The implementation result, not the browser prototype's font metrics, determines correct alignment.

- [ ] **Step 4: Verify Scheme A widths and state variants**

```powershell
npm --prefix tui test -- --run tests/components/welcome.test.tsx tests/preferences/theme.test.ts
npm --prefix tui run typecheck
```

Cover new/resumed Thread, DeepSeek/Kimi, both Thinking values, independent Local/Cloud memory combinations, Request approval/Full access, truecolor/ANSI/no-color, and narrow diagnostic behavior.

Theme tests assert the canonical dark Aurora values above and verify Welcome,
Composer, user messages, Tool activity, Picker, and status components consume
semantic roles rather than local color literals.

## Task 2: Audit Every Registered Slash Command End to End

**Files:**
- Verify: `src/awesome_agent/application/commands/`
- Verify: `src/awesome_agent/protocol/contracts.py`
- Verify: `tui/src/protocol/commands.ts`
- Verify: `tui/src/commands/catalog.ts`
- Verify: `tui/src/commands/controller.ts`
- Verify: `tui/src/commands/presenters.ts`
- Modify: command contract/audit tests under `tests/unit/application/`, `tests/unit/protocol/`, and `tui/tests/commands/`
- Create: `docs/development/command-regression.md`

**Canonical command set:**

`/new`, `/resume`, `/context`, `/compact`, `/model`, `/auth`, `/thinking`, `/workspace`, `/diff`, `/undo`, `/redo`, `/tools`, `/skills`, `/mcp`, `/memory`, `/status`, `/usage`, `/doctor`, `/config`, `/permissions`, `/init`, `/help`, `/theme`, `/copy`, `/quit`.

No `/skill`, `/workplace`, `/debug`, `/test`, `/commit`, `/review`, `/editor`, `/clear`, or `/exit` compatibility entry may remain registered or documented.

- [ ] **Step 1: Add an exhaustive command audit test**

For every canonical command, assert:

- exactly one registered name and owner;
- exact typed payload or typed Interaction;
- executable bare usage in Tab completion;
- argument placeholders only in Help usage, never inserted into the Composer;
- a visible terminal state for success, progress, interaction, empty result, cancellation, invalid context, and failure as applicable;
- no Presenter falls back to object stringification;
- Help contains one aligned row per command and is derived from the catalog;
- unknown/deleted commands return one understandable error instead of silence.

- [ ] **Step 2: Add the final command behavior table**

`docs/development/command-regression.md` records, for each command: owner, input form, result kind, interaction if any, empty behavior, error behavior, and focused automated test. This is verification documentation for contributors, not a second runtime registry. Make the file explicitly point to the generated catalog as authority.

- [ ] **Step 3: Run the command contract gates**

```powershell
uv run pytest tests/unit/application/test_command_contracts.py tests/unit/protocol tests/integration -q
npm --prefix tui test -- --run tests/commands tests/components/local-commands.test.tsx tests/components/status-command.test.tsx
```

If a command is absent or returns the wrong typed result, correct the owning earlier PR contract rather than adding a PR8-only branch.

## Task 3: Run the Complete Keyboard and Interaction-Owner Matrix

**Files:**
- Verify: `tui/src/app/TerminalInput.tsx`
- Verify: `tui/src/app/interaction-orchestrator.ts`
- Verify: `tui/src/components/Picker.tsx`
- Verify: `tui/src/components/AuthPicker.tsx`
- Verify: `tui/src/components/SecretInput.tsx`
- Verify: `tui/src/components/InteractionPrompt.tsx`
- Verify: `tui/src/components/TrustPrompt.tsx`
- Modify/add tests under: `tui/tests/app/`, `tui/tests/components/`, `tui/tests/composer/`

- [ ] **Step 1: Express input ownership as a parameterized test matrix**

Cover this priority order:

1. Fatal recovery;
2. Trust;
3. Approval and permission escalation;
4. Secret input;
5. Picker;
6. Slash command menu;
7. Composer;
8. Global lifecycle keys.

For each owner, exercise Enter, Tab where meaningful, Esc, Up, Down, Ctrl+O, and Ctrl+C. Assert exactly one owner receives a key. Disabled Picker rows cannot be selected. Esc closes or cancels only the highest-priority active interaction and restores Composer focus when appropriate.

- [ ] **Step 2: Exercise approved flows**

Cover:

- `/` opens the complete menu; Up/Down scroll through all rows; Tab inserts only the executable command name; Enter runs; Esc closes;
- `/help` is ordinary history output and does not trap input;
- Trust uses the branded Scheme A choice panel and cannot leak keys to Composer;
- Approval sends the selected result to Core, releases `waiting_for_approval`, and restores Composer focus;
- Request approval and Full access use human-readable badges and the approved base/warning variants;
- `/auth` Provider/source/secret/replace/delete flow, masked input, disabled missing Environment source, explicit selected-source-unavailable warning, no automatic fallback, and no secret history/log entry;
- `/memory` first selects Local memory or Cloud memory - Mem0, then selects On/Off, with independent values;
- `/new` and `/resume` atomically replace the active transcript;
- Ctrl+C cancels an active operation and the next message can be submitted successfully;
- Ctrl+O expands/collapses the current global details group without mutating durable state.

- [ ] **Step 3: Run interaction tests**

```powershell
npm --prefix tui test -- --run tests/app tests/components tests/composer tests/lifecycle
```

No component-local `useInput` bypass may be introduced to fix a failing case.

## Task 4: Prove Transcript, Markdown, and Agent Activity Behavior

**Files:**
- Verify: `tui/src/transcript/`
- Verify: `tui/src/components/transcript/`
- Verify: `tui/src/markdown/`
- Modify/add tests under: `tui/tests/transcript/`, `tui/tests/components/transcript.test.tsx`, `tui/tests/markdown/`

- [ ] **Step 1: Add one cross-feature transcript regression**

Simulate:

1. immediate local user message;
2. live Thinking chunks;
3. Assistant segment;
4. multiple different Tools with an intervening Thinking interval;
5. a second Assistant segment;
6. final Worked duration;
7. durable reconciliation;
8. cancellation;
9. a successful next Turn;
10. resume of durable history.

Assert stable unique keys throughout; completed Thinking remains one folded row with measured duration; all Tools between Assistant segments form exactly one folded Tool Sequence row; Ctrl+O expands actual bounded details and known omission count; Worked remains visually distinct; current-session safe details survive reconciliation; resumed history shows only durable summaries and never invents missing reasoning/detail/Worked data.

- [ ] **Step 2: Preserve Markdown feature coverage**

Verify ordinary and CJK tables, inline formula `S = πr²`, block formula, formula inside table cells, and table/formula tokens split across streaming chunks. Incomplete structural blocks remain stable until safe to render; final output reuses the same semantic AST/renderer. Unsupported LaTeX remains readable source rather than disappearing or being falsely typeset.

- [ ] **Step 3: Run transcript and Markdown gates**

```powershell
npm --prefix tui test -- --run tests/transcript tests/components/transcript.test.tsx tests/markdown
```

Any duplicate React-key diagnostic is a failure. Do not add diagnostic folding as a substitute for identity correctness.

## Task 5: Verify Thread Replacement and Change-Command Lifecycles

**Files:**
- Verify: `tui/src/state/reducer.ts`
- Verify: `tui/src/components/transcript/Transcript.tsx`
- Verify: PR6 command lifecycle modules and presenters
- Modify/add focused tests under `tui/tests/state/`, `tui/tests/components/`, and `tui/tests/integration/`

- [ ] **Step 1: Test atomic Thread changes**

Assert `/new` creates and activates the new Thread, replaces current visible transcript, clears session-only permission/detail/operation state, and displays one `New conversation started` system notice. `/resume` restores only the selected Thread entries. Late events from the old Thread cannot repopulate the new transcript. Welcome is not replayed on each switch.

- [ ] **Step 2: Test final Change Journal presentation**

Assert:

- `/compact` shows one visually distinct in-place progress row and replaces it with `Context compressed`;
- `/diff` renders a real Diff or explicit no-changes empty state;
- `/undo` and `/redo` render operation-specific success/no-op/error outcomes;
- changed paths/details are folded by default and share Ctrl+O;
- raw IDs and internal enum/error names are not the primary user-facing content.

- [ ] **Step 3: Run focused flow tests**

```powershell
npm --prefix tui test -- --run tests/state tests/integration tests/components/local-commands.test.tsx
```

## Task 6: Verify and Document the Developer Source Startup Flow

**Files:**
- Verify: `src/awesome_agent/development/launcher.py`
- Verify: `pyproject.toml`
- Verify: `.gitignore`
- Modify: `tests/unit/development/test_launcher.py`
- Modify: `docs/getting-started/quickstart.md`
- Modify: `docs/getting-started/quickstart.zh-CN.md`
- Modify: `docs/development/README.md`
- Modify if needed for navigation only: `docs/README.md`

**Approved developer journey:**

1. Install Git, uv, and Node.js 22 or newer; Python is managed by uv and npm comes with Node.js.
2. Clone the repository and enter it.
3. Run `uv sync --locked --extra memory`.
4. Run `npm ci --prefix tui`.
5. Run `uv run awesome-dev` to open the repository itself, or `uv run awesome-dev --workspace <project-path>` to open another project.
6. Complete normal workspace trust and configure credentials through the same TUI `/auth` flow as an installed build.
7. After source changes, stop and rerun the same command; hot reload is deliberately absent for active-Thread correctness.

- [ ] **Step 1: Strengthen launcher contract tests**

Assert the launcher:

- rejects a non-source checkout;
- validates Node 22+, npm, editable `awesome-core`, installed TUI dependencies, and existing workspace;
- builds the current TUI source before launch;
- launches the Node TUI in the selected workspace while the TUI privately starts Python Core;
- injects the editable Core executable directory into only the child PATH;
- defaults `AWESOME_HOME` to `<repository>/.awesome-dev/home` and reserves `<repository>/.awesome-dev/logs`;
- honors an intentional `AWESOME_HOME` override without writing it into the project;
- never creates `.awesome-dev` inside the target workspace;
- forwards normal exit codes and Ctrl+C;
- returns clear actionable prerequisite/build failures without printing credentials.

Verify `.awesome-dev/` is ignored and no tracked file exists below it.

- [ ] **Step 2: Rewrite the developer section for clarity**

Keep the consumer Quickstart's five onboarding steps intact. Add or revise a clearly separate `Develop from source` section with friendly explanations, copyable commands for Windows PowerShell and macOS/WSL shell where syntax differs, what each step does, where data/logs live, how installed and development modes differ, how to restart after code changes, focused test commands, production build command, and concise troubleshooting.

Do not describe the architecture as a user burden or imply two manually managed servers. State plainly that `uv run awesome-dev` builds the TUI and starts the current source through the normal private Core/TUI process relationship.

Mirror the behavior in `quickstart.zh-CN.md`; fix stale or corrupted text rather than copying English mechanically.

- [ ] **Step 3: Run launcher and documentation checks**

```powershell
uv run pytest tests/unit/development/test_launcher.py tests/structural/test_markdown_links.py -q
git ls-files .awesome-dev
```

Expected: tests pass and `git ls-files .awesome-dev` produces no output.

## Task 7: Align User Documentation with the Final Product

**Files:**
- Modify: `README.md`
- Modify: `README.zh-CN.md`
- Modify: `docs/user-guide/commands.md`
- Modify: `docs/user-guide/configuration.md`
- Modify: `docs/user-guide/workspace-and-tools.md`
- Modify: `docs/user-guide/memory-skills-and-mcp.md`
- Modify: `docs/user-guide/troubleshooting.md`
- Modify if boundaries changed during PR1-PR7: `ARCHITECTURE.md`, relevant `docs/architecture/` files

- [ ] **Step 1: Document behavior from the final catalog and contracts**

Update user documentation for:

- all and only the 25 canonical commands;
- bare Tab completion and argument examples;
- Scheme A keyboard controls and Ctrl+O global details;
- Request approval and Full access;
- Trust, Approval, cancellation, recovery, and new/resumed Threads;
- explicit credential source selection, Environment read-only status, Awesome-managed key replacement/deletion, and no silent fallback when a selected source becomes unavailable;
- Local memory and Cloud memory - Mem0 as independent switches;
- Tool Sequence folding and durable-summary limitation after resume;
- Thinking duration and Worked duration as locally measured UI state;
- Markdown tables and readable formula support without claiming full mathematical typesetting;
- installed versus source-development data locations.

Do not expose internal enum values, implementation-plan terminology, obsolete commands, deleted architecture, or credentials.

- [ ] **Step 2: Check language and navigation consistency**

Ensure English/Chinese README and Quickstart pairs promise the same behavior. Use natural product language in each language. Validate every relative documentation link and every documented command against the catalog-derived audit.

- [ ] **Step 3: Run documentation structural tests**

```powershell
uv run pytest tests/structural/test_markdown_links.py tests/structural -q
```

## Task 8: Execute the Final Targeted Regression Matrix

**Files:**
- Modify: `tui/tests/e2e/product-flow.test.ts`
- Modify: `tui/tests/fixtures/fake-core.mjs`
- Modify/add only targeted Python integration tests needed for PR1-PR8 flows
- Update: `docs/development/command-regression.md` with evidence references

- [ ] **Step 1: Extend the networkless product flow**

Use deterministic fake Providers and isolated temporary Awesome homes. Cover at minimum:

- first startup and Trust;
- `/auth` add, selected source, replace, delete, unavailable selected source, and immediate state refresh;
- `/model` DeepSeek and Kimi consistency across Welcome, Status, actual request, and injected model identity;
- immediate user message, streaming Markdown, Thinking, Tool Sequence, Approval, and final Worked;
- a deterministic request to create one simple file stops after the successful
  write when no validation was requested; no fixed `execute` Tool follows;
- `/context`, `/usage`, `/status`, `/doctor`, `/workspace`, `/tools`, `/skills`, `/mcp`, `/config` visible typed results;
- `/memory` Local and Mem0 independent toggles;
- `/compact`, `/diff`, `/undo`, and `/redo` lifecycle states;
- `/new` clean transcript and `/resume` correct history;
- active cancellation followed by successful continued conversation;
- fatal recovery Reconnect invoking the real controller rather than a dead menu item;
- no credential in protocol fixtures, transcript, stderr tail, SQLite, or rendered output.

Do not call live DeepSeek, Kimi, or Mem0 services in this regression.

If the minimum-action test fails, trace the graph observation and stop-condition
path in `src/awesome_agent/agent/`. Correct the Agent-owned invariant and its
focused tests before continuing; do not suppress the extra Tool in the TUI or
Tool executor.

If model identity fails, trace `modeling` identity resolution and `context`
instruction assembly; do not insert a canned self-description in the TUI. If
Reconnect fails, wire the existing lifecycle controller into the fatal recovery
owner; do not keep a visible menu action whose handler returns without work.

- [ ] **Step 2: Run validation in risk order**

```powershell
uv run ruff format --check .
uv run ruff check .
uv run mypy
npm --prefix tui run format:check
npm --prefix tui run lint
npm --prefix tui run typecheck
uv run pytest tests/unit tests/structural -q
npm --prefix tui test -- --run
uv run pytest tests/integration -q
npm --prefix tui test -- --run tests/e2e/product-flow.test.ts tests/e2e/stdio-purity.test.ts
```

Stop at the first lower-level failure unless it is proven unrelated. Full live-provider, cross-host installer, performance, and release suites are deferred release evidence and must be listed as unverified rather than silently implied.

- [ ] **Step 3: Manually inspect representative terminal widths**

Using the source launcher and fake/local deterministic configuration, inspect 80, 100, and 120 columns for Welcome, Help/menu scrolling, Status, Context, Usage, Doctor, Picker, Permission warning, Thinking, Tool Sequence, Compact, and Worked. Record terminal type, width, and observed result in the PR body. Do not replace automated assertions with screenshots.

## Task 9: Remove Replaced Paths and Complete the PR

**Files:**
- Delete only obsolete files identified by PR1-PR8 ownership checks
- Update: `.codex/pr-bodies/pr8-welcome-docs-regression.md`

- [ ] **Step 1: Run structural searches for forbidden leftovers**

Search for:

- the deleted command names;
- handwritten duplicate command arrays;
- `Record<string, JsonValue>` as a command-result contract;
- `[object Object]` rendering paths;
- internal permission enum text in presentation;
- `flexGrow` on the Logo panel;
- duplicate per-command input listeners;
- old separate Status state;
- random or fixed non-unique Assistant keys;
- old Tool grouping by same Tool name;
- `.codex` paths used as Awesome product data;
- tracked `.awesome-dev` content;
- docs that instruct developers to publish/install a release to test source.

Classify every match. Delete only replaced logic/tests. Keep valid domain uses and record why they remain.

- [ ] **Step 2: Inspect scope and secrets**

```powershell
git diff --check
git status --short
git diff --stat
git diff --name-only
git grep -n -E "(sk-|m0-|API[_ -]?KEY=.{8,})" -- . ":(exclude)package-lock.json"
```

Confirm no secret, private configuration, generated cache, debug output, HTML prototype artifact, or unrelated file is staged.

- [ ] **Step 3: Write the PR body**

Include:

- summary by Welcome, commands, interactions, transcript/activity, developer startup, and docs;
- exact validation commands and results;
- the 25-command audit link;
- 80/100/120-column manual evidence;
- deferred live/network/cross-host/release checks;
- known limitations, especially durable summary-only Tool history and readable-not-typeset advanced LaTeX;
- confirmation that no compatibility path or parallel legacy presenter remains.

- [ ] **Step 4: Commit, push, open, and merge**

```powershell
git add -- <reviewed-files>
git diff --cached --check
git diff --cached --stat
git commit -m "feat: complete terminal interaction redesign"
git push -u origin codex/pr8-welcome-docs-regression
gh pr create --base codex/tui-command-visual-consistency --head codex/pr8-welcome-docs-regression --title "Complete terminal interaction redesign" --body-file .codex/pr-bodies/pr8-welcome-docs-regression.md
gh pr checks --watch
gh pr merge --merge --delete-branch
```

Merge only when required checks pass, the PR is conflict-free, and the diff is scoped.

- [ ] **Step 5: Verify integration head**

```powershell
git switch codex/tui-command-visual-consistency
git pull --ff-only
git status --short --branch
git log -8 --oneline
```

Expected: PR1-PR8 are present in order and the integration branch is clean. Merging to `main` or publishing a release requires a separate explicit decision.

## Completion Checklist

- [ ] Welcome implements the approved Scheme A with the exact repository Logo and theme colors.
- [ ] The Logo panel is intrinsic width; the details panel receives spare width.
- [ ] Welcome is correct at 80, 100, and 120 columns and remains usable at supported narrower widths.
- [ ] All 25 canonical commands have one authority, exact typed outcomes, correct Help/completion, and visible terminal behavior.
- [ ] Deleted/obsolete command names are absent from runtime, autocomplete, Help, docs, and tests.
- [ ] Enter, Tab, Esc, Up, Down, Ctrl+O, and Ctrl+C obey one-owner routing.
- [ ] Auth, Memory, Permissions, Approval, Trust, and recovery use shared interaction components.
- [ ] No selected credential source silently falls back when unavailable.
- [ ] `/new` and `/resume` replace transcripts atomically without mixed Thread history.
- [ ] Thinking, mixed Tool sequences, detail folding, Worked, reconciliation, and resume follow PR7's durable-data boundary.
- [ ] Markdown tables and readable formulas survive streaming and final rendering.
- [ ] A completed simple write request stops without an unsolicited shell command.
- [ ] Compact, Diff, Undo, and Redo use their approved lifecycle/result designs.
- [ ] `uv run awesome-dev` is tested and documented as the one-command source launcher after dependency installation.
- [ ] Development state uses ignored `.awesome-dev`, never `.codex` or the target workspace.
- [ ] README and Quickstart language pairs are behaviorally consistent.
- [ ] Targeted validation evidence and deferred release evidence are explicit.
- [ ] No replaced legacy logic, compatibility layer, secret, generated artifact, or debug code remains.
