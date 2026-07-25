# Tools and changes

Tools are the only path from model intent to workspace or host effects. Awesome
therefore centralizes registration, argument validation, hard-deny checks,
permission decisions, approval, timeout, cancellation, audit, and terminal
events before calling a built-in or MCP handler.

The Change Journal is adjacent but separate. It records and restores file
mutations made through managed built-ins; it cannot make arbitrary shell or MCP
effects reversible.

## Tool contract

A `RegisteredTool` combines four internal facts:

- a provider-visible `ToolSpec` including name, description, JSON schema,
  capability, read-only flag, and display metadata;
- a strict Pydantic input model;
- one async handler;
- an optional dynamic total-timeout resolver.

The timeout resolver is intentionally not part of the model-visible schema. It
lets `execute` reserve `timeout_seconds + 10` seconds for bounded cleanup and
lets MCP use a 40-second outer envelope without teaching the model about
executor internals.

The built-in baseline is:

| Tool | Capability | Managed file changes |
| --- | --- | --- |
| `ls`, `read_file`, `glob`, `grep` | `workspace.read` | none |
| `write_file`, `edit_file` | `workspace.write` | journaled |
| `delete` | `workspace.delete` | journaled |
| `execute` | `shell.execute` | observation only |

The registry is extensible; eight is not a fixed maximum. MCP namespaces are
replaced atomically with names such as `mcp.<server>.<tool>`.

## Executor pipeline

```text
ToolRequest
  -> resolve registry item
  -> validate Pydantic arguments
  -> validate built-in path syntax
  -> compute non-disableable hard deny
  -> PermissionPolicy: allow | ask | deny
  -> resolve bound approval, when asked
  -> resolve total timeout
  -> invoke handler under deadline
  -> normalize result or expected failure
  -> write one ToolActivity
  -> emit one terminal tool event
  -> return one bounded ToolResult
```

`tool.started` is emitted before resolution so an unknown tool is still an
observable attempted call. Argument errors, policy denials, timeouts, and
expected handler failures become bounded `ToolResult` errors. An unexpected
handler exception is an invariant failure and terminates the Turn rather than
being disguised as a model-correctable error.

Cancellation finalizes a single cancelled activity/event with a bounded cleanup
attempt, then re-raises the caller's original cancellation. Handler tasks that
ignore cancellation are detached only after their grace deadline and their
result is consumed to avoid leaking task exceptions.

Tool content is bounded before it enters Agent state or the transcript. Audit
summaries retain argument names, not raw argument values.

## Permission decision

Permission is a pure capability decision. Hard denial always wins:

| Mode | Read | Create/modify | Delete | Shell | MCP/unknown |
| --- | --- | --- | --- | --- | --- |
| Request approval | Allow | Ask | Ask | Ask | Ask |
| Accept edits | Allow | Allow | Ask | Ask | Ask |
| Full access | Allow | Allow | Allow | Allow | Ask |

This table applies to Agent tool calls using the selected Thread's permission
session. Direct `! command` input is the user's explicit authorization for that
exact command: Application creates a separate Direct Operation with an
independent Full-access permission session, so it does not show the ordinary
shell approval. Direct execution still passes the same schema, command circuit
breaker (both lexical and pre-spawn), Process Runner, audit, timeout,
cancellation, and redaction boundaries.

An allow-once result applies to the current Tool call. “Allow all edits during
this session” grants only `workspace.write`; it cannot grant delete, shell, or
an extension capability. Mode and Thread transitions clear temporary grants.

The Tool Executor creates approval text from validated operation facts. The TUI
renders that typed request and returns a decision; it never infers capability
from prompt text or performs the operation.

## Workspace path admission and use

File tools accept workspace-relative paths. Syntax validation rejects absolute
paths, parent escapes, sensitive credential/key paths, and ambiguous Windows
spellings. `resolve_workspace_path()` then checks the canonical workspace
identity and uses `lstat` on every traversed component, refusing links and
reparse points that would be followed.

Path admission alone cannot protect a later mutation from replacement races.
Managed file handlers therefore use identity-bound primitives from
`core/filesystem.py` and `core/changes/filesystem.py`:

```text
validate lexical path
  -> open and pin workspace root
  -> descend and pin parent identities without following links
  -> capture target existence, type, identity, link count, content, and mode
  -> persist mutation intent
  -> mutate through pinned parent
  -> recapture and verify intended after-state
  -> append committed FileChange
  -> clear pending intent
```

Regular files with multiple hard links are rejected because a workspace path
cannot prove the location of every alias. Write and edit use atomic sibling
replacement. Recursive delete inventories and binds the complete tree before
the first removal; any nested symlink, junction, reparse directory, hard-linked
file, capacity violation, or observed identity change aborts with zero intended
deletions.

A final POSIX symlink node may itself be deleted without following its target;
this does not permit traversal through a linked parent or recursive inventory
through a nested link.

This is a fail-closed userspace defense, not filesystem compare-and-swap or a
kernel jail. A same-privilege process can still race after the final identity
check. Pinned no-follow operations prevent following a replacement link outside
the workspace, but cannot promise isolation from an adversarial concurrent
host writer.

## Command circuit breaker

Both Agent `execute` and direct `!` input call the same pure command policy. The
executor's pre-approval check receives the command, explicit shell dialect,
workspace, and the requested lexical working directory joined beneath the
canonical workspace root. The handler then resolves and identity-checks that
directory; its pre-spawn check calls the same policy with the verified resolved
directory. Sharing the evaluator prevents rule drift, while the second stage is
the one backed by opened-path evidence.

Bounded CMD, POSIX shell, and PowerShell inspection expands known wrappers,
compound commands, pipelines, and newlines. It normalizes executable paths,
case, and executable suffixes; tracks conservative possible working directories
across directory changes; decodes PowerShell encoded commands; handles
`Start-Process` elevation aliases; and inspects selected literal Python `-c`
calls for dangerous filesystem/process APIs.

The circuit breaker always rejects recognizable catastrophic operations such
as recursive deletion of a filesystem or workspace root, shutdown/reboot,
elevation, disk formatting, block-device overwrite, and fork bombs. An input
that cannot be parsed safely within depth and node limits is denied.

The policy is designed to prevent accidents and known wrappers. It is not a
malware detector and does not claim to understand arbitrary hostile
obfuscation. Full access cannot disable it.

## Shell lifecycle and audit

`execute` validates parameters and policy before recording an irreversible
attempt. Immediately before Process Runner startup it appends a redacted
`ExecuteObservation` to the open ChangeSet. Therefore timeout, cancellation,
spawn failure, and backend failure conservatively retain evidence; malformed
arguments, approval denial, and hard denial do not claim an execution attempt.

```text
validated + approved execute
  -> record observation
  -> spawn supervisor and root command
  -> concurrently drain bounded stdout/stderr
  -> root completes | requested timeout | cancellation | backend failure
  -> terminate owned process tree when needed
  -> force-kill after grace when needed
  -> bounded pipe drain, cancel inherited readers if needed
  -> return ProcessResult or propagate original cancellation
```

The requested `timeout_seconds` covers spawn and root command execution. The
handler's outer deadline adds 10 seconds for process-tree termination and pipe
cleanup. A command timeout returns `TIMEOUT` with metadata; the outer deadline
exists for a backend that violates its contract.

On POSIX each command uses a lease-bound session supervisor and process group.
On Windows a waiting supervisor is placed in a nested kill-on-close Job Object
before it may create the target. Root lifetime and pipe lifetime are separate:
a descendant that holds stdout open may cause output truncation, but cannot
keep the Tool call pending forever.

A process that deliberately escapes its POSIX session or otherwise acts through
external services is outside this cleanup boundary. Process ownership is not
execution isolation.

## ChangeSet model

Each Turn or direct command opens one workspace-bound ChangeSet. It starts
`OPEN`, seals to `APPLIED`, and may transition to `UNDONE` and back to
`APPLIED`. Reversibility is:

- `FULL`: managed file mutations only;
- `PARTIAL`: managed file mutations plus unmanaged execution observations;
- `NONE`: only unmanaged effects or no restorable file state.

A `FileChange` stores independent before/after node types, hashes, blob IDs,
modes, and a mutation identity. Independent node types preserve transitions
between file, directory, symlink, and absence without interpreting one side
using the other side's type.

The journal limits one ChangeSet to 1,000 file records and 50 MiB of referenced
content. Content-addressed blobs avoid repeating identical snapshots.

## Ordinary mutation crash window

The journal orders durable intent before the workspace effect:

```text
save before/after blobs
  -> save PendingMutation
  -> mutate workspace
  -> verify actual after-state
  -> save FileChange with mutation_id
  -> delete PendingMutation
```

If the process stops in this window, startup reconciliation compares recorded
identity, current workspace state, and any committed FileChange. A mutation ID
makes the “record committed, pending cleanup not committed” case idempotent.
Ambiguous legacy evidence is preserved as a conflict rather than duplicated or
discarded.

## Undo and redo transaction

Undo/redo merges repeated path changes, binds all targets in one pinned
workspace tree, and checks every current snapshot before modifying anything.

```text
load and validate blobs
  -> bind all paths + detect conflicts
  -> prepare all inverse/forward intents
  -> persist every pending intent
  -> restore each path through the same pinned tree
  -> commit ChangeSet lifecycle once
  -> clear pending intents
```

If an error occurs before the lifecycle commit, already-restored paths are
rolled back while the pinned tree and original snapshots remain available. If
that rollback cannot be proven, pending evidence remains. Startup recovery
finalizes a committed operation or rolls back an uncommitted partial operation;
it never guesses through fresh path resolution.

Undo refuses a current workspace that no longer matches the recorded after
state. It also reports that shell/MCP effects were not restored. The user can
inspect those facts with `/diff`, `/undo`, and `/redo`, but should not treat the
journal as a source-control replacement.

## Design tradeoffs

- Central execution adds ceremony to simple tools but gives every tool the same
  approval, timeout, event, and audit semantics.
- Strict no-link file operations reject some legitimate layouts in exchange
  for a boundary that can be explained and tested.
- Recording shell attempts before spawn can over-report an effect that never
  started, but avoids falsely claiming reversibility after an uncertain error.
- Multi-path undo favors conservative conflicts over overwriting user changes.
- Host execution preserves native developer workflows but leaves OS isolation
  as an explicit non-goal today.

## Source and test map

- Contracts and registry: `core/tools/contracts.py`, `registry.py`
- Policy and permissions: `core/tools/policy.py`, `permissions.py`,
  `command_policy.py`
- Executor: `core/tools/executor.py`
- Built-ins: `core/tools/builtins/`
- Process lifetime: `core/tools/process.py`, `core/process_lifetime.py`
- Journal: `core/changes/journal.py`, `core/changes/operations.py`
- Filesystem: `core/filesystem.py`, `core/changes/filesystem.py`
- Tests: `tests/unit/core/tools/`, `tests/unit/core/changes/`,
  `tests/integration/test_application_tools.py`,
  `tests/integration/test_change_journal.py`
