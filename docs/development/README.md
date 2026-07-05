# Development

These documents are for humans and coding agents modifying this repository.
They describe how to start work, keep changes scoped, validate behavior, and
record evidence without mixing local execution state into tracked docs.

## Start Here

- [Repository harness](repository-harness.md) defines the engineering loop.
- [Execution plans](execution-plans.md) defines `.codex/exec-plans/` usage.
- [Testing](testing.md) defines validation gates and evidence.

## Boundaries

Development docs are repository-contribution rules. They are not user docs,
runtime architecture contracts, roadmap items, or local session transcripts.

## Common Workflow

1. Confirm branch, worktree, and status.
2. Read the active execution plan when one exists.
3. Inspect directly relevant code, tests, and docs.
4. Make scoped changes.
5. Run the lightest validation set that covers the risk.
6. Record commands, results, skipped checks, and residual risk.
