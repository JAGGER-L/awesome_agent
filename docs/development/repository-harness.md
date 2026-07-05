# Repository Harness

Repository changes must be scoped, verified, and recoverable.

## Start

1. Confirm repository root, branch, and `git status`.
2. Read the active `.codex/exec-plans/active/` plan when it matches the task.
3. Read directly relevant source files, docs, contracts, and tests.
4. Choose the lightest validation set that covers the change.

## Execute

- preserve unrelated user and agent work
- prefer existing architecture and local patterns
- avoid broad refactors unless required by the task
- update documentation when behavior, configuration, startup, security, or
  architecture facts change

## Finish

Run verification, inspect the diff, record evidence, and leave the worktree in
a reviewable state.

The legacy engineering harness remains at
[engineering-harness](../engineering/engineering-harness.md) until all inbound
links move.
