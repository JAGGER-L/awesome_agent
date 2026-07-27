# Tools and changes

Tools are the only path from model intent to workspace or host effects. Awesome
therefore centralizes registration, strict argument validation, tool-owned hard
admission, capability decisions, approval, deadlines, cancellation, audit, and
terminal events before calling a built-in or MCP handler.

The Change Journal is adjacent but separate. It records and restores file
mutations made through managed built-ins; it cannot make arbitrary shell or MCP
effects reversible.

## Tool contract

A `RegisteredTool` combines eight internal facts:

- a provider-visible `ToolSpec` including name, description, JSON schema,
  capability, read-only flag, and display metadata;
- a strict Pydantic input model;
- one async handler;
- a typed description function that derives bounded operation facts from
  validated arguments;
- a hard-admission function that applies non-disableable, tool-specific checks
  to validated arguments and execution context;
- an explicit replay-safety classification;
- an optional dynamic total-timeout resolver;
- an optional handler-cancellation grace.

The description, admission, replay, deadline, and cancellation facts are
intentionally not part of the model-visible schema. The description supplies
bounded presentation and approval facts from an explicitly selected operation
target; it never receives an unvalidated raw argument map. Hard
admission owns facts such as path or command safety and cannot be overridden by
a permission mode or temporary grant. Description runs exactly once after hard
admission: any bounded metadata probe it performs is inside that admitted
operation and still precedes approval, the handler, and external effects.
Replay safety gives recovery an explicit answer instead of asking it to
recognize tool names. The timeout resolver lets `execute` reserve
`timeout_seconds + 10` seconds for its total deadline, while cancellation grace
bounds handler cleanup without teaching the model about executor internals.

The built-in baseline is:

| Tool | Capability | Managed file changes |
| --- | --- | --- |
| `ls`, `read_file`, `glob`, `grep` | `workspace.read` | none |
| `write_file`, `edit_file` | `workspace.write` | journaled |
| `delete` | `workspace.delete` | journaled |
| `execute` | `shell.execute` | observation only |

The registry is extensible; eight is not a fixed maximum. MCP namespaces are
replaced atomically with names such as `mcp.<server>.<tool>`.

## Citation transport inside Core and Agent

`core/citations.py` defines the minimal provider-neutral, immutable `Citation`
value: `id`, `title`, and `url`. The strict contract rejects unknown fields;
the ID has the bounded `S1` through `S999999` shape, the nonblank single-line
title is at most 500 characters, and the absolute HTTPS URL is at most 8,000
characters with no whitespace, control characters, or user information.
`ToolOutput.citations` and `ToolResult.citations` are tuples that default to
empty. On a successful handler return, the Executor strictly reconstructs each
citation and the output, then copies the citations into the normalized result.
Bounding or truncating the textual `content` does not discard those citations.

Agent serializes the complete `ToolResult` into `AgentState.tool_results`, so
the same citation values survive in the LangGraph checkpoint without a second,
top-level `AgentState` citation channel. This is an internal value path today:
Conversation records, Protocol v3, and the TUI wire are unchanged. The public
citation wire is deferred to the atomic Protocol v4 Web-citation change; there
is no interim compatibility adapter. This value contract alone does not assign
source IDs, validate citation markers in model prose, or render a Sources UI.

## Executor pipeline

```text
ToolRequest
  -> resolve registry item
  -> strict-validate with its registered input model
  -> run its registered hard admission
  -> derive its typed description exactly once
  -> PermissionPolicy for its registered capability: allow | ask | deny
  -> resolve bound approval, when asked
  -> resolve total deadline
  -> invoke handler under deadline
  -> normalize result or expected failure
  -> write one ToolActivity and audit summary
  -> emit one terminal tool event
  -> return one bounded ToolResult
```

Every attempted call emits exactly one `tool.started`, including unknown tools,
invalid arguments, and hard-admission failures. Calls that fail before a typed
description exists use only the registration's static presentation and never
derive a target from untrusted values; an admitted call emits its typed,
bounded presentation before capability policy or handler execution. Argument
errors, policy denials, timeouts, and expected handler failures become bounded
`ToolResult` errors. An unexpected handler exception is an invariant failure
and terminates the Turn rather than being disguised as a model-correctable
error.

This is the only execution order. The Executor invokes registration-owned
behavior uniformly; it does not branch on concrete tool names. Hard admission
and capability policy answer different questions: admission decides whether
this exact validated operation is ever acceptable, while policy decides
whether the registered capability is allowed, denied, or needs approval in the
current permission session.

Cancellation finalizes a single cancelled activity/event with a bounded cleanup
attempt, then re-raises the caller's original cancellation. Handler tasks that
ignore cancellation are detached only after their grace deadline and their
result is consumed to avoid leaking task exceptions.

Tool content is bounded before it enters Agent state or the transcript. Audit
summaries retain argument names, not raw argument values.

## Replay safety

Replay safety is registration metadata, not a property inferred by recovery.
Only a built-in whose managed local semantics prove that a repeated call is
safe may be marked replayable. MCP calls and other external or unclassified
effects are non-replayable. Recovery looks up the same name in the current
Runtime Registry and consumes that registration's metadata. Replayable work may
resume; non-replayable, missing, or unknown metadata fails closed into a
recovery interaction and is never retried automatically. The user may
explicitly choose Retry instead of the default Abort. A change to a same-named
tool's contract must therefore be managed as a checkpoint-compatibility change.
Neither the Executor nor recovery keeps a parallel list of special tool names.

## Permission decision

Permission is a pure capability decision evaluated only after registered hard
admission succeeds. A hard rejection always wins and cannot be converted to an
allow by any row in this table:

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
`execute` registration's hard-admission check receives the command, explicit
shell dialect, and workspace. It first resolves the requested working directory
as an existing, no-follow directory inside the pinned workspace, then evaluates
the command against that resolved path before description or approval. The
handler repeats both identity resolution and the same policy immediately before
spawn. Sharing the evaluator prevents rule drift, while the second check closes
changes between admission and process creation with fresh opened-path evidence.

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

- Contracts and registry: `core/citations.py`, `core/tools/contracts.py`,
  `registry.py`
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
