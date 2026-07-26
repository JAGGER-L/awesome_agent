# Storage and recovery

Awesome keeps product state local under the resolved `AWESOME_HOME`. Storage is
embedded because a terminal coding agent needs deterministic ownership and
offline startup, not a separately operated service. The design still separates
state by meaning: product records, graph checkpoints, reversible file blobs,
configuration, secrets, memory, and UI preferences have different owners and
recovery rules.

## State layout and owners

| Path | Owner | Contents | Lifetime |
| --- | --- | --- | --- |
| `state/application.db` | Application Storage | trust, Threads, entries, Turns, summaries, tool activity, ChangeSets, pending mutations, MCP enablement | durable local history |
| `state/checkpoints.db` | LangGraph adapter | unfinished `AgentState` channels | unfinished Turn only |
| `state/change-journal/` | Change Journal | content-addressed before/after blobs | while referenced |
| `state/provider-model-transaction.json` | Application + Config | bounded recovery intent joining the user default model to one Thread model | until verified reconciliation |
| `.provider-credential-transaction.json` | Application + Config | non-secret credential/source recovery intent | until verified reconciliation |
| `.provider-credential-transaction.env` | Application + Config | exact previous `.env` bytes; contains secrets | until verified reconciliation |
| `config.yaml` | Config | user-owned non-secret configuration | user controlled |
| `.env` | Config secret loader | explicitly persisted credentials | user controlled |
| `memory/USER.md` | local Memory | optional user facts | user controlled |
| `workspaces/<key>/MEMORY.md` | local Memory | optional workspace facts | workspace-scoped |
| `skills/` | Skills | user extension packages | user controlled |
| `ui.json` | Ink | theme and local presentation preferences | user controlled |
| workspace files | user and tools | primary project work | project lifetime |

The resettable boundary is exactly `<AWESOME_HOME>/state`. Configuration,
credentials, Memory, Skills, UI preferences, and project files are outside it.

## Why two SQLite databases

Application SQLite stores product facts: a user accepted a message, a Turn is
running, an answer completed, or a ChangeSet is applied. LangGraph's native
SQLite saver stores graph channels required to resume execution. Neither owner
copies the other's internal state.

Using separate databases avoids coupling product schema to framework checkpoint
shape and allows terminal checkpoints to be deleted without deleting history.
The cost is that product completion and checkpoint deletion cannot share one
transaction. Recovery explicitly converges that commit window.

## Application database transactions

Application SQLite uses WAL. Multi-query reads use deferred transactions so a
Thread page observes one coherent snapshot without reserving the only writer.
Mutations use `BEGIN IMMEDIATE`, obtaining the writer reservation before
validating and changing product state.

One important transaction is first-message acceptance:

```text
BEGIN IMMEDIATE
  -> update automatic Thread title
  -> append user transcript entry
  -> create running Turn
COMMIT
```

If any write fails, none of the three facts exists. Once accepted, a later
model failure or cancellation terminates the Turn but does not erase the user's
message or title update.

Raw provider payloads, token deltas, spinners, unbounded shell output, and
credential values are not product history. The Application database stores
bounded activity summaries. Full observations needed for unfinished execution
remain in the checkpoint.

## Schema identity

Application schema identity is independent from the product version. The
current bootstrap schema is Schema 7. Schema identity changes only when a
required table, payload interpretation, or cross-record invariant can no
longer be read safely by the current code, and identities increase
monotonically.

Optional fields can remain in the same schema when absence has a safe legacy
meaning. Change mutation identity and separate before/after node types follow
this rule. Completed older records without mutation IDs remain readable;
ambiguous crash-window evidence remains pending for diagnosis.

Awesome intentionally has no generic migration framework or historical adapter
chain. That reduces hidden compatibility behavior, but means an older schema
requires an explicit reset rather than an automatic migration.

## Read-only startup preflight

Before trust, checkpoints, Change Journal access, or writable database setup,
Storage classifies an existing Application database:

| Classification | Startup behavior |
| --- | --- |
| new | create current schema under an exclusive state lease |
| current | retain shared state lease and continue |
| older | present reset-or-exit interaction |
| newer | stop and ask the user to upgrade |
| unknown/corrupt/unreadable/locked | stop with a diagnostic |

Only an older schema offers reset. Treating a newer or unknown schema as
disposable could destroy state the current binary simply does not understand.

## State leases and reset

Normal sessions hold a shared cross-process state lease. Reset requires an
exclusive lease, so it cannot race another cooperating Awesome session.

```text
typed state-reset confirmation
  -> bootstrap lock
  -> foreground resolving lease
  -> acquire exclusive state lease
  -> validate target == <AWESOME_HOME>/state
  -> atomically rename old state directory
  -> create and validate fresh Schema 7
  -> remove replaced state
  -> downgrade to shared lease
  -> continue to workspace trust
```

If fresh initialization fails, Storage restores the original directory. Reset
removes trust, conversations, checkpoints, and Change Journal history; it
preserves every path outside `state`.

Atomic replacement describes the canonical filesystem namespace, not revoking
uncoordinated OS handles. On Windows an open database handle normally prevents
rename. On POSIX rename/unlink may succeed while a pre-existing handle remains
attached to the detached old inode; a new open through the canonical path sees
only fresh state. The state lease is the cooperating process contract.

## Workspace session leases

Activation holds two exclusive leases:

- a path-key lease for the canonical workspace spelling;
- an entity-key lease derived from the opened root directory identity.

The first remains stable if the pathname is replaced. The second collapses
aliases that open the same directory. Together they prevent another Core from
mistaking a live session for crash recovery through either path replacement or
alternate spelling.

The workspace identity itself is rechecked before activation and file access.
If the root object changes, the session fails rather than trusting the new
object under an old decision.

## Turn checkpoint contract

LangGraph checkpoints are keyed by Turn ID. A resumable checkpoint contains a
strict `AgentState`, including identity, budgets, messages, frozen context
manifest, pending tool progress, usage, and termination facts.

Recovery validates more than “JSON parses”:

- Thread, Turn, workspace, provider, model, and Thinking identity;
- counter values against the product Turn's configured budgets;
- message roles, tool-call/result order, and active-tail indices;
- context manifest shape, content hashes, token estimates, and transcript
  coverage;
- whether final answer and termination fields form a legal state;
- whether a pending tool can represent an uncertain external result.

An invalid checkpoint fails that Turn with a stable code. It is never repaired
by guessing a missing graph transition.

## Cross-database convergence

The Application database stores a frozen context manifest projection because
it is a product-visible fact. The checkpoint stores the same manifest because
the graph must resume from it. A crash can commit one before the other.

The latest strictly valid checkpoint is the fact source for an unfinished Turn.
Its projection may update Application state only when:

1. the product projection is empty, or its immutable source anchors share the
   same lineage;
2. the current row still equals the expected old value;
3. the candidate state passes full Turn and context validation.

```text
read unfinished Turn + latest checkpoint
  -> validate checkpoint identity and graph invariants
  -> compare frozen source anchors
  -> compare-and-swap Application projection
  -> finalize, resume, ask, or fail
```

A concurrent third value produces `context_snapshot_conflict`. Recovery
continues with other Turns rather than treating one corrupt record as a global
database failure.

This is crash convergence, not hostile local-state attestation. If an attacker
can replace both checkpoint content and its matching hashes, there is no second
external authority that authenticates them.

## Provider model cross-store transaction

Changing `/model` updates two independently durable facts: the default for new
Threads in `config.yaml` and the selected model of the current Thread in
`application.db`. Neither file can join the other's transaction. Treating one
write as best-effort compensation would make a process kill indistinguishable
from a successful half-update, so Application uses a small write-ahead journal
and one user-config resource lock as the ordering boundary:

```text
acquire config resource lock
  -> reject any unresolved journal
  -> persist PREPARED(previous values, target values)
  -> replace and reload config.yaml
  -> patch only the Thread model field in Application SQLite
  -> verify both durable values
  -> persist COMMITTED
  -> remove journal
release lock
  -> publish the verified configuration to the live runtime
```

A new process reconciles this journal before loading trusted configuration.
`PREPARED` means restore both previous values; `COMMITTED` means write both
target values. Both paths are idempotent, verify the two stores, and remove the
journal only after verification. A malformed journal, failed reconciliation,
or failed in-process compensation produces `recovery_required`. The live
runtime then rejects new Turns, Direct commands, credentials, interactions,
and other state mutations while still permitting bounded snapshots,
cancellation, and shutdown. It never guesses which side won and never replays a
Provider call.

The journal is strict UTF-8 JSON, capped at 4 KiB, contains model and Thread
identities but no credentials, and rejects links, reparse points, hard links,
non-regular files, duplicate keys, non-finite numbers, and identity drift while
opening. Its file and directory are synchronized around journal replacement.
`config.yaml`, SQLite, and the journal still have no common power-loss commit
primitive, so this contract proves ordinary process-crash convergence, not
whole-machine power-loss atomicity.

## Provider credential cross-file transaction

An Awesome-managed credential has two independently durable parts: its value
inside the complete `.env` document and its selected source in `config.yaml`.
Atomic replacement protects either file from partial bytes, but it cannot make
the pair atomic. `/auth` therefore acquires the config and secret resource locks
in one order and runs this write-ahead protocol:

```text
acquire config lock, then .env lock
  -> reject unresolved Provider journals
  -> snapshot the complete previous .env
  -> persist the secret backup, then PREPARED with whole-file hashes
  -> atomically replace .env and verify the target hash
  -> persist SECRET_COMMITTED
  -> update the selected source in config.yaml and reload
  -> verify both durable facts
  -> persist COMMITTED
  -> remove the journal and backup
release locks
  -> publish the verified configuration to the live runtime
```

The JSON journal never contains a secret. The companion backup contains the
exact previous `.env` bytes rather than only the changed key; this preserves
comments, unrelated services, ordering, and the difference between an absent
and an empty file. Both files live directly under `<AWESOME_HOME>`, outside the
resettable `state/` namespace, because reset must not erase unresolved recovery
evidence.

Startup reconciles this transaction before the first real config/secret load,
before state preflight or reset, and before workspace trust. `PREPARED` is
conservatively treated as possibly having already changed `.env`, so both
`PREPARED` and `SECRET_COMMITTED` restore and verify the complete previous file
and source. `COMMITTED` verifies the target file and rolls the source forward.
Missing, malformed, linked, over-limit, or hash-inconsistent evidence fails
closed with `recovery_required`; no phase guesses from the current secret.

Cancellation adds one same-process boundary. If a blocking mutation ignores
cancellation past the cleanup deadline, the event-loop thread installs a
Provider-configuration fence before returning cancellation. Even if that late
worker subsequently commits and removes its journal, that process does not
publish its stale snapshot or accept another mutation; a fresh process reloads
the verified durable result. The same abandonment fence covers model and
credential transactions.

A cleanup error after a verified `COMMITTED` record has already been removed
cannot be reported as a failed RPC with stale runtime state and no recovery
evidence. The mutation either publishes the verified result or retains the
runtime fence. As with the model journal, file and directory synchronization
prove bounded ordinary process-crash convergence, not a common whole-machine
power-loss commit across the two user files.

## Recovery decisions

The coordinator classifies an unfinished Turn:

- completed valid graph state: finish product persistence and delete the
  checkpoint;
- valid unfinished state: offer or perform the bounded resume flow;
- uncertain `execute` or MCP boundary: require explicit Abort/Retry with Abort
  first;
- missing, corrupt, inconsistent, or unrecoverable state: mark failed with a
  stable diagnostic;
- checkpoint belonging to an already-terminal Turn: remove stale checkpoint.

Retry is never implied by opening a Thread. Replaying an uncertain shell or MCP
call could duplicate an external effect, so the choice is bound to that Thread
and Turn and must be made explicitly.

## Change Journal durability

Change metadata and pending intents live in Application SQLite; blobs live in
`state/change-journal`; effects happen in the project filesystem. These three
locations cannot share a single transaction.

The journal writes content blobs before publishing their IDs, persists intent
before mutation, verifies the result, then records the committed change. Undo
and redo persist all intents before their first restore and commit one lifecycle
transition after all restores succeed. Startup reconciliation uses pending
evidence to finalize or roll back what it can prove.

SQLite uses WAL with `synchronous=NORMAL`. Blob files are synchronized before
replacement, but the database, blob directory, and workspace have no shared
directory-fsync boundary. An abrupt power loss may leave a conservative pending
conflict or an unrecoverable durability gap. The journal claims ordinary
process-crash reconciliation, not whole-machine power-loss atomicity.

## Failure and recovery table

| Failure point | Preserved evidence | Recovery |
| --- | --- | --- |
| before Turn transaction commits | none of first-message facts | submit may be retried |
| after running Turn commits, before checkpoint | product Turn without valid checkpoint | fail with stable recovery code |
| checkpoint commits, projection does not | validated frozen manifest | lineage-bound compare-and-swap |
| answer persists, checkpoint remains | terminal product Turn | remove stale checkpoint |
| Provider model journal is `PREPARED` | previous and target model identities | restore and verify both previous values |
| Provider model journal is `COMMITTED` | previous and target model identities | write and verify both target values |
| Provider model journal is invalid or cannot reconcile | journal remains; runtime stays unpublished or fenced | fail with `recovery_required`; do not mutate or guess |
| Provider credential journal is `PREPARED` or `SECRET_COMMITTED` | exact previous `.env` backup, source identities, whole-file hashes | restore and verify the complete previous file and source |
| Provider credential journal is `COMMITTED` | target `.env` hash and source identity | verify the target file and roll the source forward |
| Provider credential evidence is invalid or cannot reconcile | journal/backup remains; runtime stays unpublished or fenced | fail with `recovery_required`; do not load the half-state |
| mutation intent persists, effect uncertain | PendingMutation + blobs | verify, finalize, or roll back |
| shell/MCP transport fails after dispatch | conservative observation / uncertain tool state | explicit Abort or Retry |
| state reset fresh initialization fails | renamed original directory | restore original namespace |

## Design tradeoffs

- Embedded SQLite removes service operations but makes local file ownership and
  locking part of the product contract.
- Separate product/checkpoint databases preserve boundaries but require strict
  convergence.
- Explicit destructive reset is less convenient than migration, but avoids
  silently reinterpreting state.
- WAL and `synchronous=NORMAL` favor interactive performance over a claim of
  power-loss atomicity across databases and workspace files.
- Conservative pending evidence may require manual diagnosis; deleting it
  would erase the only proof of an uncertain mutation.

## Source and test map

- Database schema: `storage/database.py`
- Conversations and trust: `storage/conversations.py`, `storage/trust.py`
- Checkpoints: `storage/checkpoints.py`
- Compatibility and reset: `storage/compatibility.py`,
  `storage/state_recovery.py`
- Cross-process lease: `storage/state_lease.py`
- Change persistence: `storage/changes.py`, `core/changes/`
- Turn recovery: `application/turns.py`
- Provider model transaction: `config/model_transaction.py`,
  `application/provider_configuration.py`
- Provider credential transaction: `config/credential_transaction.py`,
  `config/credentials.py`, `application/provider_configuration.py`
- Tests: `tests/unit/storage/`, `tests/integration/test_sqlite_checkpoints.py`,
  `tests/integration/test_agent_recovery.py`,
  `tests/unit/config/test_model_transaction.py`,
  `tests/unit/config/test_credential_transaction.py`,
  `tests/unit/config/test_user_state_concurrency.py`,
  `tests/integration/test_composition_activation.py`,
  `tests/integration/test_state_reset_concurrency.py`,
  `tests/structural/test_storage_architecture.py`
