# Repository Engineering Harness

## Scope

This harness constrains Codex and any other development agent changing the
`awesome_agent` repository. It is repository governance, not product runtime
behavior.

Specific task plans, session handoffs, and raw validation output are local
state under ignored `.codex/`. Reusable rules, scripts, architecture, and
decisions remain tracked.

## Engineering Loop

```text
read repository rules and local state
-> verify environment and baseline
-> select one scoped milestone
-> implement the minimum coherent change
-> update affected documentation
-> run layered validation
-> repair from evidence
-> persist durable decisions
-> leave a recoverable local handoff
```

## Harness Layers

| Layer | Tracked mechanism | Local mechanism |
| --- | --- | --- |
| Instructions | `AGENTS.md`, architecture, designs | none |
| Environment | `uv.lock`, bootstrap, doctor | local credentials |
| State | Git history, durable decisions | `.codex/exec-plans/` |
| Scope | plan format, WIP=1 rule | active local milestone |
| Feedback | lint, types, tests, startup, E2E | raw command output |
| Documentation | impact matrix, sync checker | impact declaration |

## Mandatory Protocol

### Start

1. Read `AGENTS.md`, the active local plan, and relevant designs.
2. Inspect Git status and recent history.
3. Run health and baseline checks.
4. Record baseline failures before feature work.
5. Confirm scope, exclusions, validation, and documentation impact.

### Execute

1. Keep WIP at one milestone.
2. Make the minimum coherent change.
3. Update required documentation in the same change.
4. Run targeted checks after each logical unit.
5. Record out-of-scope findings instead of expanding scope silently.
6. Isolate parallel writes with worktrees or equivalent boundaries.

### Verify

1. Run formatting and lint.
2. Run type checking.
3. Run unit and structural checks, including documentation synchronization.
4. Run integration, startup, and E2E checks when applicable.
5. Persist commands, results, and unverified paths in the local plan.
6. Never report an unexecuted check as passing.

### Finish

1. Remove temporary output.
2. Update local plan evidence, handoff, risks, and next action.
3. Extract durable decisions into tracked documents.
4. Confirm the standard start path still works.
5. Leave a reviewable and recoverable worktree.

## Governance

Recurring review findings should become executable checks rather than longer
instructions. Remove duplicate, obsolete, unmeasured, or conflicting rules.
Track unresolved repository-level gaps in
`docs/project-governance/tech-debt-tracker.md`.
