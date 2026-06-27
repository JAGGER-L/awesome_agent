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

Current v1 team mode is explicit. It is selected with CLI `--team` or API
`mode: "team"` and routes to `team-coding@1`. Intake still creates only the
Leader; the graph creates Teammates, the Verifier, and Subagents later when the
team path starts.

## Teammates

Teammates have durable run-scoped identity, checkpointed context, mailbox
access, assigned task ownership, and an isolated worktree when writing.
Teammates may communicate directly and may create up to three Subagents without
Leader approval.

Teammates and the Verifier default to `deepseek-v4-flash`.

In `team-coding@1`, each Teammate receives a Leader assignment containing
`allowed_tools`, `allowed_skills`, write permission, delegation permission,
Subagent limits, and acceptance criteria. The runtime registers and executes
only the tools granted by that assignment; unauthorized tool requests are
rejected before reaching the central executor.

## Subagents

Subagents execute bounded tasks with isolated context. They do not read the team
mailbox, communicate with the user, or create descendants. They return
structured results, evidence, artifacts, and optional patches only to their
owning Teammate.

Subagents default to `deepseek-v4-flash`. All defaults are configurable by
agent kind, and a profile-specific override takes precedence. The resolved
model is stored on the Agent record and exposed through the inspection API.

## Current Durable Team Runtime

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

## Future Distributed Team Runtime

The long-term design promotes Teammates from graph-internal sessions to child
Runs. The Leader Run will create Teammate child Runs, each child Run can be
claimed by an independent Worker, and the Leader will aggregate verified
results through explicit lineage. That design requires parent/child Run status
propagation, cross-Run cancellation, checkpoint coordination, and result
aggregation before it can replace the scoped v1.

## Limits

```yaml
max_teammates: 6
max_subagents_per_teammate: 3
delegation_depth: 1
max_model_calls: 8
max_tool_calls: 12
max_sandboxes: 6
```
