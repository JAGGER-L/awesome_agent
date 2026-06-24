# Harness Engineering

## Purpose

The harness converts open-ended agent behavior into a constrained engineering
loop:

```text
read rules and state
-> verify environment
-> select one scoped milestone
-> implement the minimum change
-> run layered validation
-> repair from evidence
-> persist state and evidence
-> leave a recoverable handoff
```

## Harness Layers

| Layer | Repository mechanism |
| --- | --- |
| Instructions | `AGENTS.md`, architecture and topic designs |
| Environment | pinned runtime, `uv.lock`, bootstrap and doctor commands |
| State | active execution plan, task tree, events, Git history |
| Scope | WIP=1, milestone contract, exclusions and acceptance criteria |
| Feedback | lint, types, tests, startup, E2E, verification reports |

## Mandatory Protocol

### Start

1. Read `AGENTS.md`, the active plan, and relevant designs.
2. Inspect Git status and recent history.
3. Run health and baseline checks.
4. Stop feature work if the baseline is unhealthy.
5. Select one milestone and confirm scope, exclusions, and validation.

### Execute

1. Keep WIP at one milestone.
2. Make the minimum coherent change.
3. Run local checks after each logical unit.
4. Diagnose failures from actual output.
5. Record out-of-scope findings instead of silently expanding work.
6. Isolate parallel writes with worktrees or equivalent boundaries.

### Verify

1. Run static checks.
2. Run unit and integration tests.
3. Run startup and key behavior.
4. Run end-to-end tests for cross-component changes.
5. Persist commands, results, and evidence.
6. Never treat an unexecuted check as passing.

### Finish

1. Clean temporary output.
2. Update plan status, evidence, handoff, risks, and next action.
3. Update designs and decisions when boundaries changed.
4. Confirm the standard start path still works.
5. Leave a reviewable and recoverable worktree.

## Machine Enforcement

The implementation must progressively add:

- dependency-boundary tests
- state-transition tests
- schema and documentation checks
- command approval tests
- sandbox boundary tests
- verification-gate tests
- health, startup, and E2E scripts

Recurring review findings should become checks rather than longer instructions.

## Governance

Harness rules require regular review. Remove duplicate, obsolete, unmeasured,
or conflicting rules. Track harness failures in the technical-debt tracker and
measure whether added constraints prevent a demonstrated failure mode.

