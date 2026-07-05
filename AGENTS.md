# Agent Guide

This document constrains all coding agents that modify this repository, including Codex, Claude Code, and other automated development agents.

## Core Principles

* Repository files are the source of truth. Chat history may be used only as context; it must not replace verification against the current code, documentation, tests, and plan files.
* Prefer small, explicit changes. Do not perform unrelated refactors, opportunistic optimizations, or scope expansion.
* Preserve the work of the user and other agents. Do not discard, overwrite, move, or revert changes unrelated to the current task.
* When multiple worktrees or branches are active in parallel, modify only the files and plans owned by the current task. Do not affect other active worktrees, branches, or tasks.
* Do not weaken acceptance criteria to make the task pass. Do not claim implementation is complete without recorded verification evidence.
* Do not commit secrets, credentials, private configuration, temporary files, debugging code, large full-source dumps, or large raw tool outputs.

## Start Work

Before editing, perform enough context checks to understand the task without over-auditing the repository:

1. Confirm the repository root, current branch, and `git status`.
2. If `.codex/exec-plans/active/` contains a plan related to the current task, read that plan first.
3. Read the design documents, interface contracts, test files, and call chains directly relevant to the current task.
4. If the task affects code behavior, confirm the available baseline check commands. If no canonical command exists, choose the closest lightweight check and record it.

Do not perform a full-repository audit by default at the start of every task. Expand the reading scope only when the task involves architecture, public interfaces, cross-component behavior, or unclear context.

## Planning and Scope

* If an execution plan already exists, follow it. Do not re-evaluate whether the plan is reasonable unless you find a clear conflict between the plan and the code, interfaces, or documentation.
* If no execution plan exists, decide whether a short plan is needed based on task complexity. Small localized changes may be implemented directly.
* Keep changes within the current task scope.
* When you find out-of-scope issues, record them as follow-up items. Do not fix them opportunistically unless they block the current task.
* Each worktree maintains its own plan state. Do not move, close, or rewrite plan files belonging to other active worktrees.

## Product Thinking

For product-facing work, think from three angles before implementing:

* First principles: identify the underlying problem, invariant, or user need instead of only patching the visible symptom.
* Productization: prefer solutions that are coherent, reusable, documented, testable, and aligned with the product architecture.
* User experience: evaluate setup, first run, common workflows, error messages, recovery paths, and whether the behavior is understandable to a real user.

Do not turn every task into a broad redesign. Apply this thinking to guide implementation choices, then keep the actual change within the current task scope. Record larger product, architecture, or UX issues as follow-up items.

## `.codex/` Rules

* Store development plans, handoff notes, and temporary session state under `.codex/`.
* Do not commit `.codex/` content by default, unless explicitly required by the user or repository rules.
* `.codex/exec-plans/active/` should contain only the current active plan. If explicitly requested by the user, it may also contain the next approved plan.
* Accepted but not-yet-started plans belong in `.codex/exec-plans/pending/`.
* Completed, merged, or otherwise closed plans belong in `.codex/exec-plans/completed/`.
* Local execution plans are development coordination state; they do not define the behavior of the `awesome_agent` product runtime.

## Code Change Rules

* Reuse the existing architecture, module boundaries, naming style, and error-handling patterns.
* Before changing public interfaces, configuration, persistence formats, CLI/API behavior, or AgentLoop behavior, confirm the relevant design documents and callers.
* Do not use temporary compatibility layers to hide real interface inconsistencies unless explicitly required by the plan or the user.
* Do not introduce new production dependencies unless explicitly allowed by the plan or the user.
* Do not leave behind debugging code, temporary logs, one-off scripts, or unused code.
* Be restrained with large files. When adding complex functionality, prefer extracting it into new modules and keeping tests close to the logic being verified.

## Documentation Rules

After changing code, determine whether the behavior, interfaces, configuration, or architecture described in the documentation also changed.

The following changes usually require documentation updates:

* User-visible behavior changes
* CLI/API parameter, output, or error-semantics changes
* Configuration option, environment variable, or default-value changes
* Architecture boundary, module responsibility, or call-chain changes
* Installation, startup, deployment, or development-command changes

Pure bug fixes, internal refactors, test additions, and non-behavioral cleanups do not require artificial documentation edits.

If user-facing entry documentation needs to be updated, keep `README.md` and `README.zh-CN.md` consistent in the same change.

## Validation Rules

Choose the lightest validation set that sufficiently covers the risk of the current change. Use the following priority order:

1. formatting and lint
2. type checking
3. targeted unit tests
4. affected structural tests
5. affected integration tests
6. application startup or basic smoke validation
7. end-to-end tests for cross-component behavior

Validation requirements:

* Documentation-only changes usually do not require code validation; when relevant, check links, headings, and example commands.
* Localized implementation changes should run at least the relevant lint, typecheck, and targeted tests, depending on the commands available in the repository.
* Public interfaces, configuration, persistence, CLI/API entrypoints, AgentLoop, tool execution, or cross-component changes require higher-level validation.
* Dependency, packaging, startup, or deployment changes require startup or basic smoke validation.
* If a lower-level validation gate fails, do not continue to heavier validation unless the failure is unrelated to the current change and has been clearly recorded.
* If a validation gate does not exist, record it as unavailable instead of silently skipping it.

Record the commands actually run, their results, and any unverified risk areas in the execution plan, handoff notes, PR description, or final response.

## Safety and Execution Boundaries

* Host execution is only for routine repository commands and must not include clearly destructive operations.
* Obtain explicit consent before deleting files, rewriting history, cleaning large directory areas, modifying production configuration, accessing external services, or executing unknown scripts.
* Do not write secrets, credentials, private paths, full source files, or large raw tool outputs into long-term memory, plan files, or handoff notes. Record only necessary summaries and key error lines.

## Commits, PRs, and Merges

* Each commit should correspond to one completed, verified, and clearly scoped logical change.

* Do not commit speculative, partially completed, unverified, or out-of-scope work.

* Before committing, inspect `git diff` and `git status` to confirm there are no temporary files, debugging code, secrets, or unrelated changes.

* For tasks that are completed, verified, and clearly scoped, the agent may commit, push the branch, and create a PR without asking the user for confirmation again.

* A PR may be automatically merged only when all of the following conditions are satisfied:

  * The current task is complete.
  * Required validation has passed, and verification evidence has been recorded.
  * The PR contains only changes within the current task scope.
  * The branch is synchronized with the target branch, and there are no merge conflicts.
  * CI or repository-required checks have passed, or it is clear that no such checks exist.
  * There are no secrets, debugging code, temporary files, or unrelated changes.
  * The current task does not require an independent Verifier, manual review, or final user confirmation.
  * The change does not involve production configuration, deployment flow, security boundaries, permission models, data migrations, destructive operations, or major public API changes.

* If automatic push, PR creation, or merge fails, do not repeatedly retry destructive operations. Record the failure reason, current branch state, and recommended next step.

* PR or handoff notes should include: change summary, validation commands, results, unverified risks, and follow-up items.

## Finish Work

Before ending, confirm that:

1. The current changes remain within task scope.
2. Relevant code, tests, and documentation have been validated according to risk level.
3. Validation commands, results, and unverified risks have been recorded.
4. Temporary files, debugging code, and unrelated changes have been removed.
5. `git status` is explainable, and the worktree is reviewable and recoverable.
6. If the task is complete, verified, and satisfies the commit, PR, and merge conditions, the commit, push, PR creation, and merge have been completed. If not, the reason and next step have been clearly recorded.


## Project Architecture

- Primary product surface: `awesome` starts a local chat-first TUI. Plain user
  messages enter the embedded `conversation-turn` runtime; surfaces submit
  intent and render events, but do not execute graphs, call providers, or run
  tools directly.
- Runtime authority: `src/awesome_agent/runtime/` owns dispatch, graph routes,
  AgentLoop integration, capability exposure, budgets, tool execution flow,
  recovery, and worker execution.
- Surface boundary: `src/awesome_agent/cli/`, `src/awesome_agent/tui/`,
  `src/awesome_agent/surfaces/`, `src/awesome_agent/client/`, and
  `src/awesome_agent/api/` are user/operator/API surfaces. They should call
  shared runtime and surface services rather than duplicating runtime behavior.
- Provider boundary: `src/awesome_agent/modeling/` defines provider-neutral
  messages, tools, streaming, usage, and execution contracts.
  `src/awesome_agent/providers/` adapts concrete providers to those contracts.
- Tool boundary: `src/awesome_agent/tools/` owns built-in tool specs,
  guardrails, approval policy, registry, executor, repository tools, shell,
  artifacts, attachments, and memory tools. Tool permission is runtime policy,
  not prompt convention.
- State boundary: local embedded state lives under the resolved `AWESOME_HOME`
  state paths. PostgreSQL-backed adapters and migrations remain available for
  API/worker modes. Runtime state must be inspected through repositories,
  diagnostics, or tests rather than inferred from chat history.
- Extension boundary: `src/awesome_agent/extensions/` discovers user/project
  skills, MCP servers, community tools, catalog snapshots, diagnostics, and
  extension config. Extension discovery does not bypass tool capability policy.

## File Architecture

### Documentation Map

- `README.md` / `README.zh-CN.md`: user-facing project introduction,
  quickstart, feature overview, and docs entry points.
- `docs/README.md`: reader-oriented documentation index.
- `ARCHITECTURE.md`: system boundaries, source layout, and dependency
  direction.
- `docs/getting-started/quickstart.md`: manual first-run path.
- `docs/user-guide/README.md`: user-facing runtime surfaces.
- `docs/operations/README.md`: local operation, readiness, diagnostics, and
  workspace guidance.
- `docs/architecture/README.md`: durable architecture design contracts.
- `docs/api/README.md`: API resource contracts.
- `docs/development/README.md`: repository development rules and validation.
- `docs/development/repository-harness.md`: repository-agent engineering rules.
- `docs/development/execution-plans.md`: local execution-plan rules.
- `docs/governance/documentation.md`: where project information belongs.
- `docs/governance/roadmap.md`: durable product and runtime roadmap.
- `docs/governance/technical-debt.md`: durable debt registry.
- `docs/governance/quality.md`: quality gates and current score.
- `docs/reference/README.md`: generated and compact reference material.
- `docs/architecture/runtime-kernel.md`: product runtime harness.
- `docs/architecture/security-model.md`: sandbox, approval, and data-safety
  rules.
- `.codex/exec-plans/active/`: ignored local current execution plans.
- `.codex/exec-plans/completed/`: ignored local completed execution plans.
- `.codex/exec-plans/pending/`: ignored local future accepted plans.

### Repository Map

- `src/awesome_agent/paths.py` and `src/awesome_agent/settings.py`: path and
  configuration resolution, including `AWESOME_HOME`.
- `src/awesome_agent/domain/`: framework-free domain models, enums, and
  transition rules.
- `src/awesome_agent/conversation/`: thread, turn, message, stream, and
  conversation-run service contracts.
- `src/awesome_agent/runtime/`: durable graph routes, conversation graph,
  AgentLoop integration, dispatch, worker execution, context, budgets,
  validation, tool exposure, and team runtime.
- `src/awesome_agent/runtime/agent_loop/`: model/tool loop contracts and
  middleware.
- `src/awesome_agent/runtime/validation/`: validation plan detection,
  execution, summaries, and gate models.
- `src/awesome_agent/surfaces/`: local runtime host/container, local surface
  client, capability/status services, and user-visible command guidance.
- `src/awesome_agent/tui/`: Textual chat UI, rendering, chat state,
  slash-router integration, pickers, and status panel.
- `src/awesome_agent/client/`: HTTP conversation client adapters.
- `src/awesome_agent/cli/`: Typer CLI, interactive entrypoint, first-run
  config, repo context, profile, and slash-command registry.
- `src/awesome_agent/api/`: FastAPI app and schemas.
- `src/awesome_agent/attachments/` and `src/awesome_agent/artifacts/`:
  attachment lifecycle, content storage, artifact metadata, and artifact store.
- `src/awesome_agent/extensions/`: extension catalog, skill, MCP, community
  tool, diagnostics, and project extension config adapters.
- `src/awesome_agent/tools/`: built-in tool specs, registry, executor,
  approval policy, repository tools, shell, and artifact tool.
- `src/awesome_agent/memory/`: built-in and external memory models, policy, and
  service.
- `src/awesome_agent/modeling/` and `src/awesome_agent/providers/`:
  provider-neutral model protocol, process model backend, execution service,
  provider adapters, model catalog, routing, and streaming.
- `src/awesome_agent/persistence/`: PostgreSQL and local persistence adapters,
  SQLAlchemy models, runtime repositories, conversations, approvals,
  attachments, artifacts, threads, tool invocations, and local state stores.
- `src/awesome_agent/repositories/`: repository policy, Git/worktree helpers,
  registry, reservations, and managed workspace services.
- `src/awesome_agent/safety/`: redaction and data-safety helpers.
- `src/awesome_agent/sandbox/`: local, AIO, process, path-mapping, worktree, and
  sandbox factory adapters.
- `src/awesome_agent/observability/`: observability facade, repository, OTel,
  and setup helpers.
- `src/awesome_agent/agents/`: agent profile definitions.
- `migrations/`: Alembic database migrations for PostgreSQL-backed modes.
- `sandbox/`: sandbox service implementation and related files.
- `scripts/`: local developer and operations helper scripts.
- `skills/`: project runtime skill packages discovered as extension inventory.
- `tests/`: unit, integration, e2e, and structural tests.
- `.agents/`: repository-level agent support material; it is not product
  runtime state.
- `.codex/`: ignored local development-agent plans and handoff state; do not
  commit it by default.
