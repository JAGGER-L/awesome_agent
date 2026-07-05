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

## Rework

Rework is bounded by runtime policy and recorded evidence. The verifier can
request another pass only inside the assignment and budget constraints.

## Related Documents

- [Runtime kernel](runtime-kernel.md)
- [Tool capabilities](tool-capabilities.md)
- [Observability](observability.md)
