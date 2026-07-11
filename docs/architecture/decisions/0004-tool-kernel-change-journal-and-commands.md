# ADR 0004: Tool kernel, Change Journal, and command boundary

- Status: Accepted and implemented
- Date: 2026-07-10

## Decision

V1 begins with eight default tools: `ls`, `read_file`, `write_file`,
`edit_file`, `delete`, `glob`, `grep`, and `execute`. They are a stable initial
contract, not a permanent ceiling. MCP, Skill, and user extensions may register
additional namespaced tools through the same registry and executor.

The executor owns schema validation, policy, timeouts, cancellation, normalized
results, events, bounded activity summaries, and change capture. Every
modifying Turn has one ChangeSet. `/diff`, `/undo`, and `/redo` are Application
capabilities. File-tool changes are conflict-checked and reversible; unmanaged
`execute` effects are partial or non-reversible.

Slash commands are typed intents. Application commands and Skill-backed
commands stay outside model reasoning; Ink-local help/theme/copy/quit remain
presentation behavior.

## Consequences

Tool renames and result changes require contract review. New surfaces reuse
commands. Workspace files and diffs are the artifact; no parallel artifact
resource exists.

## Rejected

Provider-specific tool schemas couple tools to models. A fixed total count
blocks legitimate extensions. Putting all commands in Ink duplicates product
behavior for future surfaces.
