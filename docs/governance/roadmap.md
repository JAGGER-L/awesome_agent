# Roadmap

## Product Thesis

Awesome Agent is a local coding-agent product for trusted project work. It
should feel like a reliable terminal-first assistant that can read a project,
edit files, run commands, ask for approval when risk increases, and leave
reviewable evidence.

The product optimizes for recoverability, auditability, and user control before
broader autonomy. User message input is the only product execution creation
entry for ordinary local work.

## Current Product State

Local CLI is the primary surface. `awesome` starts a chat-first TUI from the
current project directory and can run user message turns through the embedded
local runtime. Local API and Docker API modes exist for clients, inspection,
and operator workflows.

The runtime already has durable conversation turns, trusted-local guardrails,
redaction, streaming projections, model I/O isolation, thread resources,
attachments, skills/MCP inspection, memory lifecycle work, and diagnostic
surfaces. Remaining work should make those surfaces simpler, more coherent, and
more explainable rather than add unrelated product modes.

## Strategic Pillars

| Pillar | Direction |
| --- | --- |
| Local-first product | Make `awesome` the simplest and most reliable way to work in a local project. |
| Runtime authority | Keep model calls, tool execution, approvals, evidence, cancellation, and recovery behind shared runtime boundaries. |
| Capability safety | Treat tool visibility and execution as effective policy decisions, not prompt conventions. |
| Operational clarity | Make failures, runtime state, diagnostics, and recovery paths understandable without reading raw logs. |
| Extension discipline | Add skills, MCP, memory, and future providers through shared catalog, policy, and observability contracts. |

Monetary amount limits are intentionally outside the runtime kernel. Runtime
budgets are token, reasoning-token, active-time, call-count, retry, and rework
limits.

## Now

| Initiative | Outcome | Evidence |
| --- | --- | --- |
| Reader-oriented documentation | Users, operators, contributors, architecture reviewers, and maintainers have separate entry points. | Documentation structural tests and markdown link checks cover the new map. |
| Local CLI polish | README, quickstart, user guide, and operations guide present Local CLI as the default path. | First-run docs avoid internal runtime terms and provide executable checks. |
| Runtime state clarity | Operations and API docs explain diagnostics, runtime data, and thread resources without mixing them into user docs. | API and operations pages own their respective contracts. |

## Next

| Initiative | Entry criteria | Exit shape |
| --- | --- | --- |
| Provider profile expansion | DeepSeek profile behavior is documented, tested, and stable across local/API surfaces. | Additional providers enter through the same profile, readiness, streaming, usage, and error contracts. |
| Docker and sandbox normalization | Local product closure remains stable and operator docs clearly distinguish trusted local from Docker API execution. | User-facing sandbox names and settings are consistent across docs, readiness, and tests. |
| Operations evidence | Current diagnostics endpoints and CLI commands are documented under one operations/API model. | Operators can diagnose provider, extension, runtime, and recovery issues from bounded pages. |

## Later

| Theme | Direction |
| --- | --- |
| Web and multi-surface clients | Add richer clients only after thread, command, attachment, memory, and stream contracts remain stable. |
| Team intelligence | Improve planning, verifier calibration, and recovery through observable policy changes rather than hidden prompt growth. |
| Extension ecosystem | Broaden skills, MCP, and community tools through catalog and capability policy, not ad hoc adapters. |

## Active Initiatives

### Documentation System

- Problem: `docs/` mixed user docs, runtime contracts, task history, and agent
  execution rules.
- Intended outcome: a reader-oriented tree with clear source-of-truth
  boundaries.
- Non-goals: no runtime behavior change, no new product feature, no broad
  rewrite of implementation code.
- Success evidence: structural tests and markdown link checks pass.

### Local Product Entry

- Problem: older docs overexposed internal runtime language to new users.
- Intended outcome: README and quickstart explain the product path without
  requiring runtime architecture knowledge.
- Non-goals: no removal of API or Docker operator paths.
- Success evidence: README/quickstart boundary tests pass.

## Explicit Non-Goals

- Hosted multi-user deployment.
- Production authentication or authorization model.
- A web frontend before the local CLI/API contracts settle.
- Arbitrary unbounded team chat.
- Prompt-only tool permission.
- Amount-derived runtime budget gates.

## Dependencies And Sequencing

1. Keep the local CLI understandable before expanding surfaces.
2. Keep runtime authority in shared services and graph/AgentLoop boundaries.
3. Keep capability policy and executor checks shared by built-in and extension
   tools.
4. Keep operations evidence bounded and queryable.
5. Convert future themes into committed tasks only when entry criteria and
   verifiable exit evidence are clear.

## Completed Milestones

Detailed historical task notes live in
[roadmap history](archive/roadmap-history.md).

| Milestone | Summary |
| --- | --- |
| Durable runtime foundation | Run intake, dispatch, Worker execution, model protocol, checkpoints, approvals, cancellation, validation, and observability. |
| Team runtime foundation | Leader, Teammates, Subagents, Verifier, assignment-scoped tools, mailbox, patch aggregation, rework, and stress coverage. |
| Extension foundation | Versioned catalogs, skills, MCP, community tools, diagnostics, and project/user extension configuration. |
| Product surface foundation | Local CLI/TUI, conversation state, streaming, slash commands, status/config/model/memory/skill surfaces, attachments, and error handling. |

## Change Policy

Roadmap changes must update related architecture, operations, user, debt, or
quality documents when their facts change. A task may close or narrow debt only
with executable evidence from tests, health checks, traces, durable query APIs,
or documented operational checks.
