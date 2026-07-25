# Review, Undo, and Redo

This page is for reviewing Awesome's recorded file changes and restoring them
without overwriting later work. It explains what the Change Journal covers and
where its guarantees stop.

## Review the Latest ChangeSet

After a modifying Turn or direct command, run:

```text
/diff
```

Awesome selects the latest ChangeSet in the current Workspace. To inspect an
older known record:

```text
/diff <change_set_id>
```

Text files render as a bounded unified diff. Binary files show before/after
byte counts; directory and symlink records show their node-type change. An
empty result means there is no selected recorded file delta, not that an
arbitrary shell command had no effect.

## What Is Reversible

Built-in `write_file`, `edit_file`, and `delete` preserve the before and after
state needed for restoration. The journal also distinguishes files,
directories, and symlinks, so replacing one node type with another can be
represented accurately.

Shell and MCP effects are not filesystem snapshots:

- a file-only ChangeSet is fully reversible;
- a mixed built-in-file and shell ChangeSet is partially reversible;
- an execute-only ChangeSet is not reversible.

`/undo` restores only the recorded built-in file nodes. A warning on a partial
set means unmanaged shell effects were not restored.

## Undo Safely

First inspect `/diff`, then run:

```text
/undo
```

or target a specific record:

```text
/undo <change_set_id>
```

Core preflights every path before changing any path. Each current node must
match the ChangeSet's expected “after” state. If you or another process edited
one file after the Turn, `/undo` returns a Workspace conflict and changes none
of the set.

When a conflict is legitimate, do not force the old restore. Compare the
current file with `/diff` and manually integrate the desired portion in a new
Turn.

## Redo an Undone Set

After a successful undo:

```text
/redo
```

or:

```text
/redo <change_set_id>
```

Redo performs the symmetric preflight: every current node must match the
recorded “before” state. It then restores the recorded applied state and moves
the lifecycle back to `applied`.

## Why Restoration Is Multi-Phase

Restoring several paths one by one without a durable intent would leave an
unexplainable half-undo if the process stopped. Awesome instead follows:

```text
bind all targets -> verify all states -> persist pending intents
                 -> apply all paths -> verify results -> commit lifecycle
```

If an error occurs before commit, Core attempts to roll already-restored paths
back. If verification is ambiguous, it preserves pending evidence. Startup
recovery then verifies the committed result or rolls back an uncommitted
partial operation; it does not guess based on timestamps or filenames.

## Common Outcomes

| Outcome | Meaning | Next action |
| --- | --- | --- |
| Empty diff | No selected recorded file delta | Check `/status` and whether the work was shell-only |
| `change_set_not_found` | The ID does not exist in this Workspace | Use the latest set or copy the correct ID |
| `workspace_conflict` | A current path differs from the expected state | Preserve current work and integrate manually |
| `change_not_reversible` | No built-in file state can be restored | Inspect shell/MCP targets manually |
| `invalid_change_lifecycle` | The requested action does not match applied/undone state | Inspect the set and use the opposite action if appropriate |

## A Safe Review Routine

1. Keep Request approval or Accept edits for the first implementation pass.
2. Read the assistant's summary and `/diff`.
3. Run or request the relevant verification.
4. Undo only when the entire recorded file set should move together.
5. After any timeout or cancellation, inspect external effects before replaying.

For the underlying durability model, read
[Changes and recovery](../concepts/changes-and-recovery.md). File locations and
storage ownership are in [Files and state](../reference/files-and-state.md).
