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

## Current Limitations

Until task 117 lands, distributed team execution has known gaps: team role
approval is not a full durable approval/resume flow, writing Teammates may
share the root workspace, and team RoleLoop does not fully consume the active
extension catalog.

## Target Team Contract

Teammates create Subagents through `team.create_subagent`, writing Teammates
use isolated managed worktrees, team role approval resumes the original tool
invocation exactly once, and user surfaces show a team execution tree.

## Rework

Rework is bounded by runtime policy and recorded evidence. The verifier can
request another pass only inside the assignment and budget constraints.

## Related Documents

- [Runtime kernel](runtime-kernel.md)
- [Tool capabilities](tool-capabilities.md)
- [Observability](observability.md)
