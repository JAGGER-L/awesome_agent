# Agent Team and Subagents

## Leader

The Leader is the only initial agent. It creates and deletes Teammates, owns the
task tree, observes team communication, integrates results, and decides when
the run is complete. It never directly creates Subagents.

The Leader defaults to `deepseek-v4-pro`.

## Team Mode

Use team mode only when work has independent streams, distinct specialties,
meaningful parallelism, durable responsibilities, or excessive single-context
complexity.

A team contains at most six Teammates, including exactly one primary Verifier.

Team mode is explicit. It is selected with CLI `--team` or API `mode: "team"`.
Intake still creates only the Leader. Current default routing uses distributed
`team-coding`, where the Leader Run creates child Runs for Teammates and the
Verifier. Teammate-owned Subagent child Runs are created dynamically by
Teammates only when the Leader granted delegation permission and Subagent
capacity.

## Teammates

Teammates have durable run-scoped identity, checkpointed context, mailbox
access, assigned task ownership, and an isolated worktree when writing.
Teammates may communicate directly and may create up to three Subagents without
Leader approval.

Teammates and the Verifier default to `deepseek-v4-flash`.

Each Teammate receives a Leader assignment containing `allowed_tools`,
`deferred_tools`, `promoted_tools`, `allowed_skills`, write permission,
delegation permission, Subagent limits, acceptance criteria, and handoff
context. Effective tools are `allowed_tools - (deferred_tools -
promoted_tools)`, so the Leader can grant a tool but defer exposing it until a
later promotion step. In scoped `team-coding-scoped`, tools are executed inside one
Run. In distributed `team-coding`, the assignment is durable data for the
Teammate child Run; Task 22B executes model-driven assignment-scoped role loops
using only the effective tools granted by that assignment.

## Subagents

Subagents execute bounded tasks with isolated context. They do not read the team
mailbox, communicate with the user, or create descendants. They return
structured results, evidence, artifacts, and optional patches only to their
owning Teammate.

Subagents default to `deepseek-v4-flash`. All defaults are configurable by
agent kind, and a profile-specific override takes precedence. The resolved
model is stored on the Agent record and exposed through the inspection API.

## Scoped Team Runtime

Task 13 implements a real but bounded team runtime. One Run is claimed by one
Worker and executed through one LangGraph checkpoint thread. Inside that Run,
the graph creates durable internal sessions for the Leader, a backend
Teammate, a repository-explorer Teammate, one Verifier, and a backend-owned
read-only Subagent.

The v1 graph uses bounded role steps rather than an unbounded autonomous team
loop. The backend Teammate may apply patches and inspect diffs when granted
write tools. The repository explorer and Subagent are read-only. The Verifier
reviews the result; a rejection caused by model or output quality can return
the Todo to the responsible Teammate for bounded rework. Verifier execution or
external failure has a smaller retry budget.

The current implementation persists agents, Todos, runtime events, model-call
records, side-effecting tool invocations, validation reports, and observability
spans for frontend inspection.

`team-coding-scoped` is kept as a scoped runtime and regression target. It proves
team lifecycle, tool, verifier, and validation behavior inside one durable Run,
but it is not the forward production architecture for independently scheduled
Teammates or Subagents.

## Distributed Team Runtime

Task 17 promoted Teammates from graph-internal sessions to child Runs. Task 22A
replaces deterministic Leader assignment creation with a model-generated
structured `TeamPlan`. The Leader calls its configured model, validates the
JSON plan, retries once on invalid output, emits `team.plan_created` or
`team.plan_rejected`, and creates 1-3 Teammate child Runs from the accepted
plan.

The TeamPlan may grant Teammate tools, skills, write permission, delegation
permission, Subagent slot count, and acceptance criteria. It must not contain
Verifier assignments, `subagent_goals`, `delegation_guidance`, or any Subagent
task direction. The Leader may only grant `can_delegate` and `max_subagents`;
the Teammate decides whether to create Subagents through its own model/tool
loop.

The Leader creates a Verifier child Run only after Teammate assignments reach
terminal state. Independent Workers can claim each child Run through the normal
PostgreSQL dispatch path. Parent Runs release to `waiting_*` states while child
work is active and are requeued when child assignments reach terminal states.

Task 22B replaces deterministic Teammate completion with a model/tool loop.
Task 24 routes that loop through `TeamAgentLoop` middleware. `team-role` builds
the model request from the durable assignment, exposes only effective tools,
rechecks authorization before every tool execution, and records model/tool
events with team attribution. Read-only roles must collect at least one
successful repository inspection before finalizing. Writing roles may use write
tools only when `can_write=true`, must call `repo.diff` after the last write,
and produce patch artifacts from the child workspace diff for Leader
aggregation.

Task 22C adds `team.create_subagent` as an explicit Teammate-only tool. The
tool is available only when the durable assignment includes delegation
permission and remaining Subagent capacity. Arguments must include a bounded
goal, explicit non-empty read-only `allowed_tools`, a subset of assigned skills,
and acceptance criteria. The created Subagent Run has depth 2, uses
`team-role`, has no mailbox privileges, cannot delegate, and receives only the
Teammate-selected read-only tools. Replaying the same tool call reuses the
existing assignment instead of creating a duplicate child Run. Teammates release
their lease while active Subagents run, then resume with bounded Subagent
result summaries injected into their model context.

Distributed team state is stored in:

- `runs.parent_run_id`, `runs.root_run_id`, `runs.depth`, and `runs.child_role`;
- `team_assignments` for role, permissions, runtime route, status, and handoff
  context;
- `team_mailbox_messages` for route-restricted durable communication;
- `team_child_results` for summaries, patch artifact references, changed files,
  aggregation status, and failure classification.

Task 22D makes Verifier review a structured model decision that is persisted as
a child result and mailbox message. Task 24 moves Verifier prompting, provider
calls, invalid-output retry, tool exposure, and structured decision parsing into
`TeamVerificationMiddleware`; `TeamVerifierGraph` keeps sibling-result loading,
decision validation against durable state, child-result persistence, mailbox
creation, and graph result mapping. Task 22E turns Verifier rework requests into
replacement Teammate child Runs with immutable attempt lineage and bounded
rework budgets. Task 22F covers the full deterministic Worker path with real
PostgreSQL dispatch, model calls, scoped tools, Teammate-owned Subagents, patch
artifact generation, Leader patch aggregation, Verifier pass, Verifier rework,
replacement Teammate creation, mailbox evidence, runtime events, model-call
records, spans, and artifacts.

Patch aggregation is idempotent. The Leader applies Teammate patch artifacts to
the root workspace when the preimage matches; if the postimage is already
present, the patch is treated as already aggregated. Partial or conflicting
patch state still fails the parent Run and is tracked as technical debt for a
future recovery or rework path.

The implemented `team-coding` route is now the forward distributed team runtime
for local execution. Task 24 moves Leader planning, Teammate/Subagent
model/tool execution, delegation tool calls, Verifier decisions, and team
observability behind `TeamAgentLoop` and shared middleware. Remaining work is
not basic autonomy wiring; it is hardening: Teammate-local deterministic
validation, richer mailbox collaboration policy, advanced replanning, conflict
recovery, empirically tuned rework budgets, and true concurrent Worker stress
tests.

## AgentLoop Boundary

The forward distributed runtime separates durable graph coordination from
cross-cutting loop policy:

- Thin graph nodes own durable control flow, checkpoint identity, interrupts,
  resume, child waits, child-run creation, patch aggregation, result
  persistence, mailbox messages, and terminal state transitions.
- `TeamAgentLoop` owns the middleware stage boundary for team agent
  operations, model calls, and tool calls.
- Team middleware owns Leader planning policy, Teammate/Subagent model/tool
  execution policy, delegation tool handling, Verifier prompting and parsing,
  model/tool observability, and bounded structural metadata.

New features should keep this split. Durable state transitions belong in graph
modules or focused durable helpers; model/tool/delegation/verification policy
belongs behind `TeamAgentLoop` middleware.

## Limits

```yaml
max_teammates: 6
max_subagents_per_teammate: 3
delegation_depth: 1
max_model_calls: 8
max_tool_calls: 12
max_sandboxes: 6
```
