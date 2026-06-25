# Runtime Agent Harness

## Scope

This harness defines how the `awesome_agent` product behaves when it executes a
coding task in a user's project. It does not govern Codex while Codex modifies
this repository.

Runtime configuration may be supplied from `.agents/` in the target project.
Generated state belongs in PostgreSQL and the local `.awesome-agent/` data
directory.

## Runtime Loop

```text
load user task and project policy
-> inspect repository and environment
-> Leader creates a dynamic task tree
-> choose solo mode or create Teammates plus Verifier
-> execute tools through approval and sandbox policy
-> persist events, lineage, artifacts, and task revisions
-> independently verify team output
-> Leader decides whether the run is complete
```

## Enforced Boundaries

- Only the Leader exists initially.
- Only the Leader creates or deletes Teammates.
- Team mode always includes exactly one primary Verifier.
- Each Teammate may create at most three depth-one Subagents.
- Subagents have isolated context, no team mailbox access, and no descendants.
- Tool execution passes through the centralized registry and approval policy.
- Untrusted commands use Docker; trusted-local requires explicit CLI consent.
- PostgreSQL stores durable run projections and LangGraph checkpoints.
- Run business state and worker dispatch state remain separate.
- A worker may commit protected transitions only with the current fencing
  token.
- Workers heartbeat directly to PostgreSQL. API availability does not control
  lease validity.
- Expired leases requeue until the maximum claim count, then enter
  `recovery_required`.
- Every Run uses an isolated integration worktree from a clean captured base;
  read-only intent controls tools rather than bypassing isolation, and
  trusted-local does not permit direct edits to the user's checkout.
- API runs select a registered repository identity rather than an arbitrary
  filesystem path.
- Approval applies to one exact canonical tool invocation and expires.
- Full conversations, source, secrets, and raw tool output are excluded from
  memory.
- The Leader may finish team work only after independent verification passes.

## Durable Solo Execution

The target local process topology is:

```text
awesome-agent serve
awesome-agent worker
awesome-agent start
```

`serve` hosts the API, `worker` claims durable work, and `start` supervises one
of each for normal local use. API request handling never owns the lifetime of a
coding Run.

Current implementation boundary: the Worker claims only diagnostic
`runtime_probe` Runs. These probes validate lease, checkpoint, recovery, and
event plumbing without repository tools or goal execution. Coding Runs remain
queued until the model/tool loop is implemented.

LangGraph checkpoints own the resumable graph position. Project PostgreSQL
tables own user-visible projections and dispatch leases. Stable transition IDs,
tool invocation IDs, and fencing tokens prevent stale or replayed work from
silently duplicating side effects.

An irreconcilable checkpoint/projection mismatch places the Run in
`recovery_required` and preserves its workspace and evidence.

## Repository and Validation Policy

All Runs require a clean primary Git base and execute in a retained Run
worktree. V1 rejects linked-worktree registration, does not modify the current
checkout, and does not automatically delete accepted Run worktrees.

The target repository may define ordered checks in
`.agents/validation.toml`. Without configuration, the runtime may infer only
strongly evidenced check-only commands. Ambiguous, networked, installation,
migration, deployment, or write-capable commands require approval.

Solo completion requires no pending approval or tool call, an accepted diff or
explicit no-change result, and all required validation accounted for.

## Runtime Plans

Runtime plans are product data, represented by the Leader plan, Todo tree,
revisions, events, and checkpoints. They are not Markdown files under
`docs/` or `.codex/`.

Future export features may write user-facing reports under
`.awesome-agent/exports/`, but PostgreSQL remains the authoritative runtime
state.

## Runtime Documentation Discipline

When the product modifies a user's project, it should detect the target
project's documentation conventions and include documentation work in its Todo
tree when the implementation changes documented behavior. This repository's
Codex rules are not automatically imposed on unrelated user repositories.
