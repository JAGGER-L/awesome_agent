# Team Runtime

The team runtime coordinates leader, teammate, subagent, and verifier work
without changing the product entry contract: user messages still enter through
conversation runs and the leader path.

## Roles

| Role | Responsibility |
| --- | --- |
| Leader | Interpret user intent, decide whether to answer directly or delegate work. |
| Teammate | Complete scoped implementation or analysis assignments. |
| Subagent | Handle isolated specialist work when the runtime grants capability. |
| Verifier | Check whether evidence satisfies the task before completion. |

## Boundaries

Assignments carry explicit scope, allowed tools, context budget, and expected
evidence. Teammates do not inherit unrestricted repository authority from the
leader. Patch aggregation, result handoff, and rework are runtime events, not
free-form chat conventions.

## Product Route Boundary

Distributed `team-coding`, `team-role`, and `team-verifier` are the forward
product routes. `team-coding-scoped` is a compatibility route and must not be
treated as the source of truth for new product behavior.

## Subagent Contract

A Subagent is created only when a Teammate calls `team.create_subagent`.
Subagents are read-only, depth-two child Runs, report only to their creator,
and count against the creator Teammate's lifetime subagent quota.

## Writing Workspace Contract

Writing Teammates use isolated managed worktrees. The Leader aggregates child
patch artifacts back into the root workspace after child completion. Read-only
Teammates, Subagents, and Verifiers may inherit the parent workspace.

## Role Tool And Approval Contract

Team role tool calls are recorded as durable tool invocations with a
run-scoped idempotency key. Approval continuation payloads are typed around the
original tool call, message snapshot, workspace binding, and role-loop counters
so approval resume can replay the approved invocation before model re-entry.

## Extension And Skill Contract

Team RoleLoop resolves tool exposure from the active extension catalog instead
of an empty catalog. Allowed skills resolve into prompt instructions, with
missing or incompatible skills recorded as denied reasons.

## Team Tree Surface

API, CLI, and TUI surfaces should prefer the team execution tree over raw event
order when explaining Leader, Teammate, Subagent, Verifier, rework, and waiting
state.

## Rework

Rework is bounded by runtime policy and recorded evidence. The verifier can
request another pass only inside the assignment and budget constraints.

## Related Documents

- [Runtime kernel](runtime-kernel.md)
- [Tool capabilities](tool-capabilities.md)
- [Observability](observability.md)
