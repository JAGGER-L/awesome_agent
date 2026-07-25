# Storage

Awesome stores product state under the resolved `AWESOME_HOME`; no separate
database service is required.

## Ownership

- `state/application.db`: workspace trust, Threads, transcript entries, Turns,
  summaries, bounded tool activity, ChangeSet metadata, pending mutations, and
  MCP enablement.
- `state/checkpoints.db`: LangGraph-owned checkpoints through its native SQLite
  saver.
- `state/change-journal/`: content-addressed before/after blobs used by diff,
  undo, and redo.
- `memory/USER.md` and `workspaces/<workspace_key>/MEMORY.md`: optional local
  memory documents.
- `config.yaml`, `.env`, `skills/`, and `ui.json`: user configuration, secrets,
  extensions, and presentation preferences.

Application and checkpoint databases remain separate because product lifecycle
and graph channels have different owners. Workspace files remain the primary
user-visible state.

Application SQLite uses deferred transactions for multi-query Thread snapshots
and `BEGIN IMMEDIATE` for mutations. WAL readers therefore retain one coherent
view without competing for the single writer reservation; writes still acquire
that reservation before validating and changing product state.

## Durability boundary

Thread rows persist both the title and its `automatic` or `manual` provenance.
The first accepted message uses one transaction to update the automatic title,
append the user Entry, and create the Turn. A failure in any write rolls back
all three facts. Later model failure or cancellation does not undo an already
accepted first message or its title.

Token deltas, spinner state, raw provider payloads, unbounded shell output, and
credentials are not product history. Tool activity stores bounded summaries;
tool observations required for recovery remain in the unfinished Turn's
LangGraph checkpoint.

The two SQLite databases do not share a transaction. For an unfinished Turn,
the latest durable checkpoint is the recovery fact and the Application
database's context manifest is a product projection. Recovery accepts that
checkpoint only after strict Turn/workspace/model identity, budget, message
role, content-hash, token-count, and active tool-tail validation. A differing
projection is repaired with compare-and-swap only when all frozen source
anchors still match; a concurrent third value fails that Turn with
`context_snapshot_conflict` and does not stop reconciliation of other Turns.
An empty projection can be rebuilt from the same verified checkpoint.

This is crash convergence, not cross-database atomicity or hostile local-state
attestation. In particular, when the Application projection is empty, the
product-instruction message and its matching hash are both facts supplied by
the checkpoint; there is no second durable digest against which to authenticate
that content. An attacker able to replace Awesome's local checkpoint database
is outside this recovery guarantee.

Change Journal intent rows, blob files, and workspace mutations are ordered for
ordinary process-crash reconciliation, but they are not one power-loss-atomic
transaction. Application SQLite uses WAL with `synchronous=NORMAL`; blob files
are synced before replacement, while the database, blob directories, and the
workspace have no shared directory-fsync boundary. A host power loss may
therefore leave conservative pending evidence or an unrecoverable durability
gap and is outside the journal's atomicity claim.

The Application database has one current bootstrap format: Schema 7. Schema
identity is independent from the Awesome product version and changes when a
required table shape, payload interpretation, or cross-record invariant is no
longer backward-readable. Optional fields inside an existing JSON payload may
remain in the same schema when absence retains a safe legacy interpretation.
Change Journal mutation identity and separate before/after node types follow
that rule. ChangeSet JSON stores the optional fields directly; pending rows use
a versioned JSON envelope inside the existing `node_type` TEXT value and still
decode legacy scalar node types. A legacy record without mutation identity
remains valid completed history, but a matching pending crash-window record is
ambiguous and is preserved for diagnosis rather than guessed or duplicated.
Schema identities increase monotonically and are never reused.

Startup opens an existing Application database read-only before workspace
trust, checkpoints, Change Journal state, or writable database configuration.
It classifies the database as new, current, older, newer, unknown, or
unavailable:

- new and current state continue normally;
- older state opens one explicit reset-or-exit interaction;
- newer state asks the user to upgrade Awesome and never offers reset;
- unknown, corrupt, unreadable, or locked state stops with a diagnostic and is
  never silently deleted.

Awesome does not include historical adapters or a generic migration framework.
The current recovery flow is deliberately destructive only after confirmation:

```text
Ink startup prompt
    -> interaction.respond(reset_state)
    -> Application bootstrap
    -> exclusive state lease
    -> Storage atomic replacement
    -> fresh Schema 7
    -> workspace trust
```

Storage validates that the reset boundary is exactly `<AWESOME_HOME>/state`,
renames it atomically, creates and validates fresh state, and restores the
original directory if initialization fails. Normal Awesome processes retain a
shared state lease; reset requires exclusive ownership, so it cannot race an
active session.

The lease is the cross-process product contract; the rename does not revoke
uncoordinated operating-system handles. Windows sharing rules make an open
Application database handle fail before replacement. On POSIX, rename and
unlink can succeed while a handle is open: that handle continues to observe
the detached old inode until close, and a new connection through
`<AWESOME_HOME>/state/application.db` observes only the fresh database. This
platform difference does not allow the two generations to share one pathname.

Reset removes conversations, Threads, trust, checkpoints, and Change Journal
history. It preserves `config.yaml`, `.env`, `skills/`, `memory/`,
`workspaces/`, `ui.json`, and every workspace file because those paths are
outside the state boundary. Successful recovery continues to workspace trust
in the same Core process.
