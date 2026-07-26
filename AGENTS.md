# Agent Guide

This document constrains coding agents that modify this repository, including
Codex, Claude Code, and other automated development agents.

## Core Principles

- Repository files are the source of truth. Chat history is context, not a
  substitute for checking current code, documentation, tests, and plans.
- Prefer small, explicit changes. Do not perform unrelated refactors,
  opportunistic optimization, or scope expansion.
- Preserve user and agent work. Do not discard, overwrite, move, or revert
  unrelated changes.
- When branches or worktrees run in parallel, modify only the files owned by
  the current task.
- Do not weaken acceptance criteria to make a task pass. Completion claims
  require recorded verification evidence.
- Never commit secrets, private configuration, generated caches, debugging
  code, temporary payloads, or large raw tool output.

## Start Work

Before editing:

1. Confirm repository root, current branch, worktree status, and `git status`.
2. Read the current plan under `.codex/exec-plans/active/` when one exists.
3. Read the design documents, public contracts, tests, and call chains directly
   relevant to the task.
4. Identify the lightest baseline check that covers the affected behavior.

Expand the audit only when the task changes architecture, public interfaces,
cross-component behavior, or an unclear boundary.

## Planning and Scope

- Follow an accepted execution plan unless current source proves a conflict.
- Use a short plan for multi-file or cross-boundary work; localized edits may be
  implemented directly.
- Record out-of-scope findings as follow-up work instead of fixing them
  opportunistically.
- Each worktree owns its plan state and branch changes.
- `.codex/` is ignored, temporary development coordination state. Keep only the
  current active plan and explicitly accepted pending work; remove completed
  plans and task artifacts after handoff rather than maintaining a repository
  archive.
- `.codex/` content never defines Awesome product behavior.

## Product Thinking

For product-facing work, evaluate:

- **First principles:** identify the underlying user need and invariant.
- **Productization:** prefer coherent, reusable, documented, and testable
  behavior aligned with the current architecture.
- **User experience:** consider setup, first run, common workflows, errors,
  cancellation, and recovery.

Use this reasoning to guide the scoped change; do not turn every task into a
broad redesign.

## Code Change Rules

- Reuse current module boundaries, naming, and error semantics.
- Before changing public interfaces, configuration, storage formats, protocol
  behavior, or the Agent loop, confirm relevant callers and architecture docs.
- Do not add compatibility layers unless the user or accepted plan explicitly
  requires them.
- Do not add production dependencies without explicit scope.
- Do not leave debugging code, unused modules, temporary logs, or one-off
  scripts.
- Prefer focused modules when adding complex behavior, while avoiding unrelated
  file restructuring.

## Documentation Rules

Update documentation when changing:

- user-visible behavior;
- commands, protocol methods, output, or error semantics;
- configuration keys, environment variables, or defaults;
- architecture boundaries, responsibilities, or call chains;
- installation, startup, packaging, or development commands.

Keep `README.md` and `README.zh-CN.md` behaviorally consistent. Internal
refactors and test-only changes do not require artificial documentation edits.
Every public source under `docs/`, the root README, and the root architecture
overview must keep its English and `.zh-CN.md` peer behaviorally synchronized;
the site does not provide untranslated locale fallbacks or legacy route
redirects.
The homepage uses the paired `site/homepage-content.en.json` and
`site/homepage-content.zh-CN.json` sources; keep their schema, stable IDs, and
shared destinations synchronized.
After both languages are reviewed, refresh the site translation lock and
inspect its diff before running the documentation build gates.

## Validation Rules

Choose the smallest validation set that covers current risk, in this order:

1. formatting and lint;
2. type checking;
3. affected unit tests;
4. structural contracts;
5. affected integration tests;
6. startup or packaging checks;
7. end-to-end tests for cross-component user flows.

During architecture stabilization, tests protect current behavior and current
public contracts. Delete tests that only encode discarded implementation
details; do not add adapters, skips, or expected failures solely to satisfy
them. Full E2E, smoke, performance, live-provider, network, and cross-host
installer suites are release evidence once their corresponding user flows are
stable, not automatic gates for every repository refactor.

If a lower validation gate fails, stop before heavier checks unless the failure
is proven unrelated. Record commands, results, deferred coverage, and
unverified risks in the plan, PR, or final handoff.

## Safety and Execution Boundaries

- Host execution is limited to routine repository commands and known project
  checks.
- Obtain explicit consent before deleting source or user files, rewriting
  history, cleaning broad directory areas, modifying production configuration,
  accessing external services, or running unknown scripts.
- Before recursive deletion or movement on Windows, resolve absolute targets
  and prove they remain under the intended workspace.
- Do not copy secret values or private paths into plans, logs, PR bodies, or
  long-term memory.

## Commits, PRs, and Merges

- Each commit represents one complete, verified logical change.
- Before committing, inspect the diff and status for unrelated files, secrets,
  generated state, and debug output.
- Completed scoped work may be committed, pushed, and opened as a PR without
  asking for repeated confirmation.
- Automatically merge only when the task is complete, required checks pass, the
  PR is conflict-free and scoped, and no manual security/deployment/data review
  is required.
- If push, PR creation, checks, or merge fail, record the exact failure and next
  safe action; do not retry destructive operations blindly.
- PR or handoff notes include summary, validation commands/results, deferred
  checks, risks, and follow-up work.

## Finish Work

Before ending:

1. Confirm changes match the accepted scope.
2. Run and record the required validation set.
3. Remove temporary files, caches created specifically for the task, and debug
   output.
4. Confirm `git status` is explainable and the worktree is recoverable.
5. Complete the scoped commit and approved integration workflow, or state the
   blocker and next action.

## Project Architecture

- **Product surface:** `awesome` starts the Ink chat TUI and one private Python
  Core process. Ink submits typed intent and renders events; it does not call
  models, execute graphs, run tools, or write product state.
- **Application boundary:** `src/awesome_agent/application/` owns workspace
  initialization, Thread/Turn lifecycle, commands, one foreground operation,
  interactions, cancellation, recovery, composition, and event projection.
- **Agent boundary:** `src/awesome_agent/agent/` owns the only LangGraph graph,
  AgentState, nodes, routing, context/model/tool loop invariants, budgets,
  compression, and finalization.
- **Model boundary:** `src/awesome_agent/modeling/` defines provider-neutral
  messages, tools, streams, usage, errors, and the gateway.
  `src/awesome_agent/providers/` contains DeepSeek and Kimi adapters.
- **Tool boundary:** `src/awesome_agent/core/tools/` owns built-in tools,
  registry, policy, executor, normalized results, and process execution.
  `src/awesome_agent/core/changes/` owns Change Journal behavior.
- **State boundary:** `src/awesome_agent/conversation/` defines product records;
  `src/awesome_agent/storage/` implements embedded Application SQLite,
  LangGraph checkpoint, trust, conversation, MCP, and Change Journal adapters.
- **Context and extension boundary:** `src/awesome_agent/context/`,
  `src/awesome_agent/extensions/`, and `src/awesome_agent/memory/` assemble
  bounded context, Skills, MCP, local memory, and optional Mem0 Cloud. None can
  bypass tool policy.
- **Protocol boundary:** `src/awesome_agent/protocol/` exposes versioned
  JSON-RPC/NDJSON over private stdio. `tui/` is the Ink + React presentation
  package.

## File Architecture

### Documentation Map

- `README.md` / `README.zh-CN.md`: product introduction, installation, first
  use, capabilities, and documentation links.
- `docs/README.md` / `docs/README.zh-CN.md`: bilingual reader-oriented
  documentation indexes.
- `ARCHITECTURE.md` / `ARCHITECTURE.zh-CN.md`: authoritative bilingual
  topology, data flow, package ownership, state, dependency direction, and
  extension boundaries.
- `docs/getting-started/`: product orientation, installation, and the English
  and Chinese first-session path.
- `docs/concepts/`: product mental model, lifecycle vocabulary, context,
  changes, cancellation, and recovery.
- `docs/user-guide/`: task-oriented commands, permissions, tools, changes,
  configuration, and troubleshooting.
- `docs/extensions/`: Memory, Skills, and MCP decision and usage guides.
- `docs/reference/`: exact CLI, command, configuration, tool, permission,
  file/state, and Protocol v3 contracts.
- `docs/architecture/`: focused lifecycle, Application/Agent, context/model,
  tool, storage, protocol/TUI, security, and dependency guides.
- `docs/development/`: contributor setup, testing, extension, contract,
  documentation, and release guidance.
- `docs/roadmap.md`: current foundation and future product capabilities.

### Repository Map

- `src/awesome_agent/agent/`: graph, state, nodes, budgets, and routing.
- `src/awesome_agent/application/`: facade, lifecycle, commands, interactions,
  operations, composition, recovery, and event projection.
- `src/awesome_agent/config/`: configuration models, loading, precedence, and
  user configuration writes.
- `src/awesome_agent/context/`: prompt assembly, path references, token
  estimates, summaries, and compression.
- `src/awesome_agent/conversation/`: Thread, Turn, transcript, summary, and
  repository contracts.
- `src/awesome_agent/core/`: workspace identity, events, tools, policy, and
  Change Journal.
- `src/awesome_agent/extensions/`: Skills and MCP stdio adapters.
- `src/awesome_agent/memory/`: local USER/MEMORY files, Mem0 Cloud,
  distillation, identity, policy, and memory tools.
- `src/awesome_agent/modeling/` and `src/awesome_agent/providers/`:
  provider-neutral model contracts, gateway, catalog, and DeepSeek/Kimi.
- `src/awesome_agent/protocol/`: JSON-RPC messages and private stdio Host.
- `src/awesome_agent/safety/`: redaction helpers.
- `src/awesome_agent/storage/`: Application SQLite, checkpoints,
  conversations, trust, Change Journal, pagination, and MCP enablement.
- `tui/`: Node Ink + React UI, protocol client, state reducers, transcript,
  commands, composer, and presentation tests.
- `protocol/fixtures/`: deterministic cross-language protocol examples.
- `scripts/release/`: release bundle construction.
- `install.sh` / `install.ps1`: bootstrap installers.
- `tests/`: unit, integration, E2E, packaging, and structural contracts.
- `.codex/`: ignored temporary development coordination state.
