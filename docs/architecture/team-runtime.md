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
product routes. The legacy scoped team route `team-coding-scoped` has been
retired and is not executable by current Workers.

Runtime readiness requires the distributed team routes. Historical scoped run
records may remain as stored data, but non-terminal historical scoped rows
should be cancelled and recreated through `team-coding`.

## Subagent Contract

A Subagent is created only when a Teammate calls `team.create_subagent`.
Subagents are read-only, depth-two child Runs, report only to their creator,
and count against the creator Teammate's lifetime subagent quota.

## Writing Workspace Contract

Writing Teammates use isolated managed worktrees. The Leader aggregates child
patch artifacts back into the root workspace after child completion. Read-only
Teammates, Subagents, and Verifiers may inherit the parent workspace.

Isolated writing children persist the workspace path, integration branch, and
explicit workspace state returned by the allocator. Inherited workspace users
keep the parent workspace state instead of pretending they own a managed
worktree.

## Role Tool And Approval Contract

Team role tool calls are recorded as durable tool invocations with a
run-scoped idempotency key. Approval continuation payloads are typed around the
original tool call, message snapshot, workspace binding, and role-loop counters
so approval resume can replay the approved invocation before model re-entry.

Approval wait is a runtime pause, not a retryable tool failure. A pending
approval stores a durable approval row and an `approval.requested` event with a
`team_role_approval_continuation` payload. Approved resume validates the tool
version, argument hash, workspace path, workspace fingerprint, and granted
capabilities before executing the original invocation once with
`approval_granted=True`. Denied or expired approvals produce a tool-result
error and do not execute the tool.

## Extension And Skill Contract

Team RoleLoop resolves tool exposure from the active extension catalog instead
of an empty catalog. Allowed skills resolve into prompt instructions, with
missing or incompatible skills recorded as denied reasons.

The same runtime tool registry and executor used by the API/local worker
assembly execute team role repo, shell, artifact, memory, attachment, MCP, and
community tools. Team-native control tools such as `team.create_subagent` and
team mailbox calls remain in-process role-loop tools but still receive durable
tool invocation records.

## Team Tree Surface

API, CLI, and TUI surfaces should prefer the team execution tree over raw event
order when explaining Leader, Teammate, Subagent, Verifier, rework, and waiting
state.

The team tree reports effective tools, denied tool counts/reasons, pending
approval tool/risk/status, workspace isolation summary, child results, and
specific waiting reasons such as `waiting_approval` or `waiting_subagents`.

## Rework

Rework is bounded by runtime policy and recorded evidence. The verifier can
request another pass only inside the assignment and budget constraints.

## Related Documents

- [Runtime kernel](runtime-kernel.md)
- [Tool capabilities](tool-capabilities.md)
- [Observability](observability.md)
