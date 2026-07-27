# Changes and Recovery

This page explains how Awesome makes file changes reviewable and how it avoids
guessing after cancellation or a crash. It is for anyone deciding whether a
Turn is safe to retry, undo, or abandon.

## Three Different Records

Awesome does not use one database row as proof of everything:

| Record | What it proves | What it cannot prove |
| --- | --- | --- |
| Thread and Turn | The request, terminal status, answer, and usage | Exact filesystem state |
| LangGraph checkpoint | Where a resumable Agent loop stopped | Whether a pending side-effecting tool already acted |
| Change Journal | Observed built-in file before/after states and shell attempts | Invocation idempotency or arbitrary effects created by shell or MCP |

Keeping these records separate prevents a conversational “completed” flag from
being mistaken for a filesystem transaction.

## The ChangeSet Lifecycle

A modifying Operation owns one ChangeSet. Built-in `write_file`, `edit_file`,
and `delete` record before and after node type, content identity, and restoration
data. `execute` records a conservative, redacted observation before the process
runner starts.

```text
             /undo
open --> applied ------> undone
          ^                 |
          |------ /redo ----|
```

The open ChangeSet is sealed as `applied` when its owner finishes. A file-only
set can be fully reversible. A set containing both built-in file mutations and
shell execution is only partially reversible. An execute-only set is not
reversible because Awesome does not snapshot arbitrary command effects.

This is why `/undo` is not “undo the whole agent.” It restores recorded
Workspace nodes only.

## Review Before Restore

Use:

```text
/diff
/diff <change_set_id>
/undo
/undo <change_set_id>
/redo
/redo <change_set_id>
```

Without an ID, the commands select the latest ChangeSet for the Workspace.
`/diff` shows a bounded unified diff for UTF-8 text and summaries for binary,
directory, or symlink changes. `/undo` and `/redo` first verify every affected
path; no restoration starts when the current Workspace conflicts with the
recorded expected state.

The detailed user flow is in [Review, undo, and redo](../user-guide/changes.md).

## Atomicity Is Conservative

Before restore, Core binds every target, checks the complete set for conflicts,
and writes pending restoration intents. It then applies changes through the
same bounded Workspace tree and commits the lifecycle only after all paths
match the intended result.

If one path fails before commit, Core rolls back paths already restored. If it
cannot verify that rollback, it leaves pending evidence for startup recovery
instead of deleting the record. That evidence is more valuable than a falsely
clean state.

## Turn Recovery

An unexpected exit may leave a Turn `in_progress`. On the next startup, Awesome
compares the durable Turn with its checkpoint and frozen context facts.

```text
unfinished Turn
      |
      +-- verified; pending work replayable -> Retry is the safe default
      |
      +-- non-replayable tool may have acted -> Abort is the safe default
      |
      +-- checkpoint/context invalid --------> fail with a diagnostic
```

For a verified local checkpoint, Retry continues from that checkpoint; it does
not rebuild a different context from the current files. Recovery resumes
automatically only when the pending tool registration proves repetition safe.
The built-in file mutation tools are non-replayable because a user or another
process may change the same path after the crash. For an uncertain file
mutation, shell command, MCP call, or Web request, Awesome never transparently
replays or assumes failure. The user must choose between retrying the remaining
Turn with that uncertainty visible or aborting it.

Abort marks the unfinished Turn failed without continuing it. It does not roll
back the filesystem or external systems, and it does not erase Change Journal
evidence.

## Startup State Compatibility

Product upgrades can make the embedded conversation/checkpoint schema
incompatible. When the product can safely identify a resettable older state,
startup offers **Reset local state and continue** and lists what will be
removed. Reset removes conversations, trust records, checkpoints, and undo
history. It preserves API keys, user configuration, Skills, and Local or Cloud
Memory settings.

State created by a newer Awesome version is not reset by an older binary.
Unknown, corrupt, unreadable, locked, or concurrently used state produces a
diagnostic. Upgrade, close the other session, or investigate the error; do not
delete the data directory merely to bypass the check.

## Cancellation and External Effects

Ctrl+C requests bounded cleanup and preserves the original cancellation
outcome. Process-tree termination reduces orphaned children but cannot retract
a network request, a daemon that escaped its group, or a command that already
committed an external change. MCP timeout and connection loss similarly report
an uncertain outcome because the server may have acted before the connection
failed.

The safe rule is: retry pure reads freely, inspect file changes before restore,
and treat timed-out external writes as potentially successful until the target
system proves otherwise.

## Recovery Checklist

1. Read the exact prompt and identify whether the uncertainty is local,
   filesystem, shell, MCP, or Web.
2. Inspect `/status`, `/diff`, the affected files, and the external target when
   available.
3. Choose Retry only when replaying the remaining logical work is safe.
4. Choose Abort when duplicate side effects would be worse than an
   incomplete Turn.
5. Start a new Turn explaining any manually verified state.

For symptoms and error-specific steps, see
[Troubleshooting](../user-guide/troubleshooting.md). Storage details and file
locations are listed in [Files and state](../reference/files-and-state.md).
