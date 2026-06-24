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
- Full conversations, source, secrets, and raw tool output are excluded from
  memory.
- The Leader may finish team work only after independent verification passes.

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
tree. This repository's own mapping in
`docs/engineering/documentation-sync.md` is an engineering-harness rule and is
not automatically imposed on unrelated user repositories.
