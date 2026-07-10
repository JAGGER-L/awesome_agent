# ADR 0004: Fixed Tools, Change Journal, and Command Boundary

- Status: Accepted
- Date: 2026-07-10
- Scope: Tools, changes, and slash commands

## Context

Tool names, result semantics, and change tracking are contracts consumed by
models, skills, tests, and all future surfaces. Treating slash commands as TUI
implementation details would force Agent Core changes when other surfaces are
added.

## Decision

The eight default coding tools are exactly:

`ls`, `read_file`, `write_file`, `edit_file`, `delete`, `glob`, `grep`, and
`execute`.

All tools use a small registry and one mandatory executor for schema
validation, policy, execution, normalized results, event emission, and change
capture. MCP and user tools adapt to the same contract.

Every modifying turn produces one change set. `/diff`, `/undo`, and `/redo`
operate on change sets and are core product capabilities. Workspace files are
the user artifact; the target has no separate general Artifact resource.

Full reversibility covers mutations made through `write_file`, `edit_file`,
and `delete`, with conflict checks that refuse to overwrite later user edits.
Host `execute` effects are unmanaged: a mixed turn is partially reversible and
an execute-only turn may be non-reversible. Phase 1 does not take a full
workspace snapshot or claim that undo restores arbitrary shell side effects.

Slash commands are typed application intents outside the reasoning loop. The
accepted command families and direct `@path` and `!command` forms are listed in
the target architecture. Skill-backed commands select skills; Ink-local
commands remain presentation behavior.

## Consequences

- Tool renames are contract changes and require explicit review.
- Expected tool errors return normalized observations rather than leaking
  provider-specific exceptions.
- New surfaces can share commands without executing tools directly.
- Future commands extend the command service, not the agent graph control flow.

## Rejected Alternatives

- Provider-specific tool schemas: couples tools to models.
- A separate artifact database for ordinary file changes: duplicates the
  workspace and change journal.
- Put all slash commands in Ink: prevents reuse by CLI, API, and IDE surfaces.
