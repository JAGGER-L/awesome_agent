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
Verifier, and each Teammate may create bounded Subagent child Runs.

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
Teammate child Run; the first distributed skeleton records permissions and
results but does not yet execute model-driven team tools.

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

Task 17 promotes Teammates from graph-internal sessions to child Runs. The
Leader Run creates Teammate child Runs, each Teammate can create depth-one
Subagent child Runs, and the Leader creates a Verifier child Run before
finalization. Independent Workers can claim each child Run through the normal
PostgreSQL dispatch path. Parent Runs release to `waiting_*` states while child
work is active and are requeued when child assignments reach terminal states.

Distributed team state is stored in:

- `runs.parent_run_id`, `runs.root_run_id`, `runs.depth`, and `runs.child_role`;
- `team_assignments` for role, permissions, graph identity, status, and handoff
  context;
- `team_mailbox_messages` for route-restricted durable communication;
- `team_child_results` for summaries, patch artifact references, changed files,
  aggregation status, and failure classification.

The current distributed graph is a deterministic skeleton with real durable
lineage, mailbox, cancellation propagation, API/CLI inspection, and
PostgreSQL-backed integration/E2E evidence. Task 18 adds root-aware team budget
checks, deferred assignment tool exposure, and artifact-backed compaction for
large handoff, child-result, and verifier evidence payloads. Rich model-driven
role planning, team tool execution, and verifier rework loops remain later
work.

This boundary is intentional. In `team-coding`, Leader, role, and Verifier
graphs are production-wired for dispatch and persistence, but the child role
graphs currently execute deterministic assignments instead of autonomous
model/tool loops. Leader patch aggregation can apply child patch artifacts when
they exist; future model-driven role graphs must cover that path with real
patch-producing E2E tests before the runtime is described as a full autonomous
agent team.

## Future Model-Driven Runtime

The long-term runtime should move most cross-cutting behavior out of large graph
files and into explicit loop/middleware layers:

- Thin graph nodes own durable control flow, checkpoint identity, interrupts,
  resume, and terminal state transitions.
- Agent loop code owns the model/tool iteration contract and reports durable
  loop outcomes back to the graph.
- Middleware owns memory, context injection, token budgets, deferred tool
  exposure, sandbox policy, approval, skill activation, team/subagent policy,
  model/tool error handling, and context compaction.

Until that migration exists, large graph files remain the source of truth for
solo and team execution. New features should be added through focused helpers
where possible and should avoid widening the deterministic distributed team
skeleton into a partially model-driven runtime without tests.

## Limits

```yaml
max_teammates: 6
max_subagents_per_teammate: 3
delegation_depth: 1
max_model_calls: 8
max_tool_calls: 12
max_sandboxes: 6
```
