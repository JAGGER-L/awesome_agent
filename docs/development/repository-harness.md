# Repository Harness

Repository changes must be scoped, verified, and recoverable.

## Agent Rules

Repository files are the source of truth. Chat history can guide investigation
but cannot replace checking current code, tests, documentation, and plans.

Preserve unrelated user work. Do not revert, delete, move, or restage changes
outside the current task.

## Harness Layers

- source code and tests
- durable docs under `docs/`
- local plans under `.codex/exec-plans/`
- branch evidence in commits, PR notes, and final responses

## Start

Confirm repository root, current branch, and `git status`. Read the active plan
when one exists. Expand context only when architecture, public interfaces, or
unclear behavior require it.

## Execute

Keep changes scoped to the current task. Prefer existing architecture and
helper APIs. Record follow-up issues instead of opportunistically fixing
unrelated problems.

## Verify

Run formatting, lint, type checks, targeted tests, integration tests, and smoke
checks according to risk. Stop broadening validation when a lower gate fails
for task-related reasons.

## Finish

Inspect diff and status. Confirm no secrets, temporary files, debugging code,
or unrelated changes are included. Record validation evidence.
