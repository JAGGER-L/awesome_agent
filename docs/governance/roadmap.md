# Roadmap

## Product Thesis

Awesome Agent is a single-user, local-first coding agent for one trusted
workspace. It helps a developer understand, modify, and validate local code
through a terminal-first workflow.

It is not a general agent platform, hosted multi-user service, distributed job
scheduler, or workflow engine. The accepted destination is the
[Local-first target architecture](../architecture/local-first-target.md).

## Current Product State

The Phase 3 candidate combines the Python Application Facade and `awesome-core`
JSON-RPC/stdio Host with the packaged Ink + React `awesome-tui`. The existing
`awesome` Textual entry remains unchanged; Phase 4 owns default cutover and
physical legacy deletion.

The repository also contains PostgreSQL adapters, Worker and dispatch
machinery, custom durable run/recovery behavior, generalized approvals,
artifacts, team runtime, multiple sandbox modes, FastAPI surfaces, and a
Textual TUI. These are current implementation facts, not target requirements.

Existing development and test data is disposable. The architecture rewrite
does not preserve or migrate it.

For the current implementation, user message input is the only product
execution creation entry for ordinary local work. Target application commands
remain explicit user input and do not create an independent execution plane.

## Strategic Pillars

| Pillar | Direction |
| --- | --- |
| Local-first simplicity | Make installation, first trust, conversation, changes, validation, undo, and recovery work without external infrastructure. |
| Python core authority | Keep Agent Core, LangGraph, tools, memory, storage, configuration, and providers in Python. |
| Thin runtime | Let LangGraph own graph execution and checkpoints; retain only local lifecycle, cancellation, commands, and event forwarding. |
| Workspace safety | Use explicit workspace trust and executor-enforced path and command policy without a generalized approval platform. |
| Stable surface contract | Keep Ink + React, future API, and future IDE clients behind the same Python application and event boundary. |
| Disciplined extension | Keep skills, MCP, Mem0 Cloud, and future backends subordinate to the core tool, context, policy, and event contracts. |

Monetary amount limits are intentionally outside the runtime kernel. Runtime
budgets remain technical limits such as tokens, reasoning tokens, active time,
model/tool calls, retries, and rework.

## Now

| Initiative | Outcome | Exit evidence |
| --- | --- | --- |
| Target contract freeze | Product boundaries, fixed tools, commands, trust, storage, memory, events, and surface protocol have one accepted source of truth. | Architecture decision records and structural contract tests agree. |
| Phase 2 complete | Python Agent Core, the ten-method Application Facade, and the protocol-only `awesome-core` stdio Host are implemented. | Focused unit, integration, fake DeepSeek/Kimi E2E, structural, manual protocol smoke, lint, type, and lock gates pass. |
| Phase 3 complete | The packaged `awesome-tui` candidate owns terminal input/rendering over the real stdio Host without TypeScript business logic. | TypeScript gates, tarball smoke, structural boundaries, and networkless DeepSeek/Kimi product flows pass. |

## Next

| Initiative | Entry criteria | Exit shape |
| --- | --- | --- |
| Product cutover | The packaged Ink candidate closes first-run trust, chat, tools, changes, memory, diagnostics, cancellation, and recovery workflows. | `awesome` switches to Python Core + Ink without redesigning the accepted protocol. |
| Legacy removal | All current callers have moved to target boundaries and required product tests pass. | Superseded runtime, persistence, API, team, artifact, approval, sandbox-service, migrations, and documentation are deleted rather than retained behind compatibility layers. |

## Later

| Theme | Direction |
| --- | --- |
| Optional Docker backend | Add Docker only as a tool execution backend after local host execution policy and contracts are stable. |
| API and IDE surfaces | Adapt stable Python application, command, and event contracts; do not create a second runtime authority. |
| Additional memory services | Revisit only when a second real service is required; Mem0 Cloud is the only supported external memory in the accepted target. |
| Advanced workflows | Background work, parallel agents, worktree orchestration, schedules, and remote execution require separate product evidence and decisions. |
| Extension distribution | Consider catalogs or marketplaces only after local skills and MCP configuration are stable and understandable. |

## Active Initiatives

### Local-first Core Rewrite

- Problem: the current platform-oriented architecture creates infrastructure,
  state, operations, and maintenance work that a local coding agent does not
  need.
- Intended outcome: Python Agent Core + LangGraph + thin runtime + SQLite +
  fixed tools + dual-layer memory, exposed first through a headless path and
  then through Ink + React.
- Migration stance: progressive in the current repository, with no legacy data
  migration and no permanent compatibility architecture.
- Non-goals: no all-TypeScript rewrite, new HTTP API, Docker sandbox backend,
  hosted mode, team runtime, or generalized Agent Platform.
- Success evidence: each phase has an independently usable vertical slice and
  explicit deletion gate.

## Explicit Non-Goals

- Hosted multi-user deployment.
- A distributed Worker, lease, heartbeat, dispatch, or recovery platform.
- PostgreSQL as a local product dependency.
- Preservation or migration of current development data.
- A generalized approval, artifact, event-store, or memory-provider framework.
- A web or HTTP API before the local application contract settles.
- Docker as a first-phase requirement.
- Prompt-only tool permission or trust.

## Dependencies And Sequencing

1. Freeze contracts before moving implementations.
2. Build and verify the headless Python product path before the Ink TUI.
3. Keep every phase independently testable; do not switch all entry points at
   once.
4. Delete old paths only after replacement acceptance gates pass.
5. Do not add data migration, dual-write, or compatibility work for disposable
   development state.
6. Update current-state architecture and user documentation as each product
   path actually changes.

## Completed Milestones

Detailed historical task notes live in
[roadmap history](archive/roadmap-history.md).

| Milestone | Summary |
| --- | --- |
| Local-first foundation | SQLite state and checkpoints, workspace trust, eight fixed tools, Change Journal, typed commands/events/interactions, cancellation, and a fresh-state headless acceptance slice without external infrastructure. |
| Python Agent Core and stdio Host | Surface-neutral ten-method Facade, LangGraph Turn path, Provider/Tool/Context/Skill/MCP/memory composition, direct commands, JSON-RPC v1, and networkless DeepSeek/Kimi subprocess flows. |
| Ink product candidate | Node 22 `awesome-tui`, Mint frameless Welcome, native scrollback, Unicode composer, typed commands/status, trust, cancellation/reconnect, isolated stdio channels, and clean npm packaging. |
| Durable runtime foundation | Run intake, dispatch, Worker execution, model protocol, checkpoints, approvals, cancellation, validation, and observability. |
| Team runtime foundation | Leader, Teammates, Subagents, Verifier, assignment-scoped tools, mailbox, patch aggregation, rework, and stress coverage. |
| Extension foundation | Versioned catalogs, skills, MCP, community tools, diagnostics, and project/user extension configuration. |
| Product surface foundation | Local CLI/TUI, conversation state, streaming, slash commands, status/config/model/memory/skill surfaces, attachments, and error handling. |

These milestones describe repository history. They do not require the accepted
target to retain each subsystem.

## Change Policy

Roadmap changes must update related architecture, operations, user, debt, or
quality documents when their facts change. A task may close or narrow debt only
with executable evidence from tests, health checks, traces, durable query APIs,
or documented operational checks.
