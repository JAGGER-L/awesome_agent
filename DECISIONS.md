# DECISIONS.md

Lightweight decision log for project-level technical choices.

Use this file for decisions that affect architecture, dependencies, agent roles, persistence, runtime behavior, or maintenance. Keep entries short and link to deeper docs when needed.

## Template

```markdown
## YYYY-MM-DD: Decision Title

Status: proposed | accepted | superseded

Context:
- What problem are we solving?

Decision:
- What did we choose?

Consequences:
- What gets easier?
- What tradeoffs or risks did we accept?
```

## 2026-06-23: Keep agent docs at repository root

Status: superseded

Context:
- Coding agents should discover project instructions and current state without searching deeply.

Decision:
- Keep `AGENTS.md`, `PROGRESS.md`, and `DECISIONS.md` in the repository root.
- Keep deeper design notes under `docs/`.

Consequences:
- Agent context is easy to find.
- Long-form docs can still move into `docs/` as the project grows.

Superseded by the 2026-06-24 Harness Engineering decision below.

## 2026-06-24: Use a short root entry and layered repository docs

Status: accepted

Context:
- Large instruction files become stale and reduce agent reliability.
- Active work needs durable scope, evidence, and handoff without empty session
  templates.

Decision:
- Keep `AGENTS.md`, `README.md`, and `ARCHITECTURE.md` at the repository root.
- Store detailed designs and product rules under `docs/`.
- Store active scope, evidence, progress, and handoff in execution plans.
- Remove root `PROGRESS.md` and `session-handoff.md`.

Consequences:
- Agents receive a short entry point and load detailed context on demand.
- Active plans become the human-readable source for progress and recovery.
- Machine-readable plan status supports deterministic WIP control.

Superseded in part by the 2026-06-25 harness-state separation decision.

## 2026-06-25: Default to DeepSeek with traceable role models

Status: accepted

Context:
- The first release needs one active provider while preserving a project-owned
  provider boundary.
- Model cost and capability should differ by agent responsibility.

Decision:
- Use DeepSeek Chat Completions as the default provider.
- Default the Leader to `deepseek-v4-pro`.
- Default Teammates, the Verifier, and Subagents to `deepseek-v4-flash`.
- Allow kind defaults and profile-specific overrides.
- Persist the resolved model on every Agent record.

Consequences:
- The inspection API can explain which model each Agent used.
- A future provider can be added without changing orchestration contracts.

## 2026-06-25: Make PostgreSQL authoritative for API projections

Status: accepted

Context:
- Process-local Run, Agent, Todo, and Event projections disappeared on restart.

Decision:
- Route runtime state through a repository port backed by PostgreSQL.
- Keep the in-memory implementation as an explicit test adapter only.
- Use the live event stream for SSE delivery, not durable history.

Consequences:
- API reads and event history survive service restarts.
- Local operation now requires migrated PostgreSQL for the default FastAPI app.

## 2026-06-25: Separate development-agent and runtime-agent state

Status: accepted

Context:
- Repository-maintenance plans were stored beside product documentation and
  could be mistaken for plans created by the `awesome_agent` runtime.

Decision:
- Store Codex and other development-agent plans under ignored `.codex/`.
- Keep reusable repository rules under `docs/engineering/`.
- Keep product runtime harness behavior under `docs/design-docs/`.
- Reserve `.agents/` for product runtime configuration.
- Store generated runtime state in PostgreSQL or ignored `.awesome-agent/`.

Consequences:
- Development history no longer appears as product runtime state.
- Durable conclusions must be extracted from local plans before completion.

## 2026-06-25: Separate run lifecycle from worker dispatch

Status: accepted

Context:
- User-visible run state and internal queue/lease state have different
  transition rules and recovery needs.

Decision:
- `RunStatus` describes product lifecycle.
- `DispatchStatus` separately describes queued, claimed, executing, waiting,
  retry, and terminal scheduling state.
- New runs begin as `created` plus `queued`.
- PostgreSQL workers claim runs with leases and monotonically increasing
  fencing tokens; Redis and Temporal are not V1 dependencies.

Consequences:
- Queue recovery does not overload the product status model.
- Every protected worker transition must validate the current fencing token.

## 2026-06-25: Divide durable execution authority

Status: accepted

Context:
- LangGraph checkpoints and project-owned PostgreSQL projections can diverge
  after partial failure.

Decision:
- LangGraph checkpoints own the next executable graph position and resumable
  agent context.
- PostgreSQL domain tables own user-visible business projections.
- Runtime events are an ordered audit log, not a replay-complete state engine.
- Stable transition IDs and idempotent projection updates support
  reconciliation; ambiguous mismatches enter `recovery_required`.

Consequences:
- Recovery uses each store for the responsibility it can represent reliably.
- Side effects require idempotency or an explicit non-retry policy.

## 2026-06-25: Register repositories and isolate every run

Status: accepted

Context:
- API-supplied arbitrary filesystem paths and direct edits to the user's
  checkout are incompatible with durable, concurrent, recoverable execution.

Decision:
- PostgreSQL stores registered repository identities; local configuration
  stores allowed filesystem roots.
- CLI paths resolve to registered repositories; API runs accept
  `repository_id`.
- V1 accepts only clean primary Git checkouts and rejects linked-worktree
  registration.
- Every read-only or modifying run uses a dedicated named integration worktree
  from an exact base commit; intent controls later tool capabilities.
- A private durable reservation precedes Git side effects. Run, Leader, initial
  events, and reservation publication commit atomically after workspace
  readiness.
- V1 never modifies the user's checkout directly and never auto-deletes a
  published Run worktree.

Consequences:
- Repository access has both identity and local authorization checks.
- Interrupted intake can be reconciled without exposing a half-created Run.
- Users must explicitly clean retained worktrees; uncommitted-change snapshots
  are deferred.

## 2026-06-25: Keep the current Run lease on the Run row

Status: accepted

Context:
- Each Run has at most one current execution owner.
- Task 03 needs atomic claim, fencing, state, and event updates without another
  local service.

Decision:
- Store current lease and retry fields directly on `runs`.
- Claim with `FOR UPDATE SKIP LOCKED`.
- Identify an owner with process-scoped worker UUID plus fencing token.
- Use PostgreSQL time, a 60-second lease, 15-second heartbeat interval, and
  three maximum claims by default.
- Keep claim/release history in ordered runtime events.

Consequences:
- Queue operations lock one authoritative row and require no Redis.
- Heartbeat writes do not represent business-state updates.
- Task 04 can add a worker without changing the queue protocol.

## 2026-06-25: Bind approvals and validation to explicit contracts

Status: accepted

Context:
- Durable resume must not reuse an approval for a changed command, and
  validation commands from a repository are untrusted input.

Decision:
- V1 approvals bind to one exact canonical tool invocation, workspace, risk,
  and capability set.
- Approval expiry defaults to 60 minutes and may be configured up to 24 hours.
- `.agents/validation.toml`, maintained by the target repository owner, is the
  primary validation command source.
- Without configuration, only strongly evidenced, check-only commands may be
  conservatively detected and run automatically in Docker; ambiguous or
  side-effecting commands require approval.

Consequences:
- Approval recovery is narrowly auditable.
- Validation remains useful for unconfigured repositories without treating
  arbitrary project scripts as trusted.

## 2026-06-25: Separate visible reasoning from private continuation

Status: accepted

Context:
- DeepSeek exposes reasoning content that must be replayed with tool calls.
- OpenAI exposes display summaries and opaque or encrypted reasoning items for
  continuation.
- The frontend needs a generic collapsible thinking experience without
  exposing provider internals.

Decision:
- Use one provider-neutral streaming protocol for reasoning, text, tool calls,
  completion, and failure.
- Store displayable reasoning in `ReasoningTrace`.
- Store provider continuation separately as opaque JSON in
  `ContinuationState`.
- Allow continuation only in checkpoints and matching provider adapters.
- Exclude continuation from public serialization, APIs, events, logs,
  artifacts, and memory.
- Present reasoning in the future frontend as generic `thinking`, without
  provider-source labels.

Consequences:
- Orchestration can switch providers without importing SDK types.
- The UI can show available reasoning without misrepresenting opaque
  continuation as readable model thought.
- Task 06 can build the model/tool loop on a stable native tool-call contract.

## 2026-06-26: Use an explicit looping graph for read-only Coding Runs

Status: accepted

Context:
- A fixed inspection workflow cannot adapt tool selection to repository
  evidence or correct model/tool mistakes.
- An unbounded loop can repeat reads indefinitely and consume uncontrolled
  context and cost.

Decision:
- Route read-only Coding Runs to `solo-readonly@1`.
- Use explicit `execute_tools -> model_turn` and `feedback -> model_turn` graph
  back edges.
- Return correctable tool failures to the model and require one successful
  repository inspection before completion.
- Apply configurable 60-turn/120-call safety boundaries, convergence reminders,
  a final no-tool turn, and no-progress detection.
- Keep realtime reasoning deltas out of PostgreSQL until throttled Task 11
  observability exists.

Consequences:
- Repository exploration is dynamic but bounded and checkpointable.
- Read-only replay is safe, while stable transition IDs prevent duplicate audit
  events.
- Modifying tools and validation remain separate roadmap tasks.
