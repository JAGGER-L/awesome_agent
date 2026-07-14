# State Schema Diagnostic Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace generic `core_request_failed` startup output for incompatible Application SQLite state with an exact, non-mutating, non-retryable product diagnostic.

**Architecture:** Storage detects the schema version without applying mutable database PRAGMAs. The Application composition boundary maps `ApplicationSchemaMismatch` to a typed product error, JSON-RPC transports that normal failure, and the TUI preserves its exact data through startup classification into a dedicated Quit-only fatal panel. Recovery remains an explicit developer action.

**Tech Stack:** Python 3.12, SQLite, Pydantic 2, JSON-RPC/NDJSON, TypeScript 7, Zod 4, React 19, Ink 7.1, Pytest, Vitest.

## Global Constraints

- Do not add Schema 1 migration, compatibility reads, downgrade handling, backup, rollback, or automatic deletion.
- Do not delete `config.yaml`, `ui.json`, credentials, or state from production code.
- `state_schema_incompatible` is non-retryable and offers only Quit; do not expose Reconnect.
- Storage owns version detection; Application owns product error semantics; Protocol transports exact facts; TUI owns copy and layout.
- The exact data contract is `found_schema: integer`, `expected_schema: integer`, and `state_directory: non-empty string`.
- A mismatch must not reach JSON-RPC's broad `except Exception` path or use `core_request_failed`.
- Recovery documentation resets only `<AWESOME_HOME>/state` after Awesome is stopped.
- Keep `README.md` and `README.zh-CN.md` unchanged unless they currently claim automatic migration or a conflicting recovery procedure.

---

### Task 1: Make schema detection non-mutating and map it at Application initialization

**Files:**
- Modify: `src/awesome_agent/storage/database.py`
- Modify: `src/awesome_agent/application/contracts.py`
- Modify: `src/awesome_agent/application/composition.py`
- Test: `tests/unit/storage/test_application_database.py`
- Test: `tests/integration/test_workspace_trust.py`

**Interfaces:**
- Produces: `ProductErrorCode.STATE_SCHEMA_INCOMPATIBLE`.
- Preserves: `ApplicationSchemaMismatch(found: int, expected: int)` as the storage exception.
- Produces on initialize: `ApplicationResult[InitializeResult].failure(ProductError(...))` with the exact three data fields.

- [ ] **Step 1: Write non-mutation and typed initialization failure tests**

Add a storage test that creates a database with `PRAGMA user_version = 1`,
records its bytes and the complete state-directory inventory, calls
`initialize_application_database()`, and asserts:

```python
with pytest.raises(ApplicationSchemaMismatch) as raised:
    initialize_application_database(database)

assert raised.value.found == 1
assert raised.value.expected == APPLICATION_SCHEMA_VERSION
assert database.read_bytes() == before
assert not database.with_name("application.db-wal").exists()
assert not database.with_name("application.db-shm").exists()
```

Add an integration test in `test_workspace_trust.py`:

```python
application = await composition.compose_local_application(
    home=home,
    workspace=workspace,
    event_sink=CollectingEventSink(),
    environ={},
)
result = await application.initialize()

assert result.ok is False
assert result.error is not None
assert result.error.code is ProductErrorCode.STATE_SCHEMA_INCOMPATIBLE
assert result.error.retryable is False
assert result.error.data == {
    "found_schema": 1,
    "expected_schema": APPLICATION_SCHEMA_VERSION,
    "state_directory": str((home / "state").resolve()),
}
```

Also assert that composing and initializing the Application creates no
`checkpoints.db`, WAL, SHM, or other state file, and that the database bytes
and sibling `config.yaml` remain unchanged.

- [ ] **Step 2: Run the tests and verify current failures**

```bash
uv run pytest tests/unit/storage/test_application_database.py tests/integration/test_workspace_trust.py -q
```

Expected: the storage non-mutation assertion fails because `_connect()` applies
PRAGMAs before checking the version; composition also opens the checkpoint
database before Application initialization; and the Application result is not
a typed failure.

- [ ] **Step 3: Separate read-only version inspection from writable setup**

Implement an exact read-only helper that never creates or configures a writable
connection for an existing database:

```python
def _schema_version(path: Path) -> int:
    database_path = path.expanduser().resolve()
    if not database_path.exists():
        return 0
    connection = sqlite3.connect(
        f"{database_path.as_uri()}?mode=ro",
        uri=True,
        timeout=5.0,
    )
    try:
        return int(connection.execute("PRAGMA user_version").fetchone()[0])
    finally:
        connection.close()
```

`initialize_application_database()` must call `_schema_version()` first. Only
Schema 0 may proceed into `_connect()` and schema creation; only current Schema
2 may return; every other nonzero value raises without calling `_connect()`.

- [ ] **Step 4: Move writable checkpoint startup behind Application preflight**

Remove `saver` from `compose_local_application()`'s eager resource setup and
from `_LocalApplicationBackend.__init__()`. Initialize these fields as absent:

```python
self._saver: BaseCheckpointSaver[str] | None = None
self._checkpoints: LangGraphCheckpointStore | None = None
```

After trust storage has successfully initialized the current Application
schema, `_activate()` opens the checkpoint saver exactly once:

```python
if self._saver is None:
    self._saver = await self._resources.enter_async_context(
        sqlite_checkpoint_saver(self._paths.checkpoint_db)
    )
    self._checkpoints = LangGraphCheckpointStore(self._saver)
```

Compile the graph and construct `TurnCoordinator` only after these assignments,
using focused assertions where a non-optional value is required. Do not add an
in-memory fallback saver or a mismatch-only backend.

- [ ] **Step 5: Add and map the product error at the composition boundary**

Add the enum value:

```python
STATE_SCHEMA_INCOMPATIBLE = "state_schema_incompatible"
```

In `initialize_application()`, catch only the trust-store access that triggers
the initialization preflight:

```python
try:
    trust_status = self._trust.status(self._workspace)
except ApplicationSchemaMismatch as error:
    raise _application_failure(
        ProductErrorCode.STATE_SCHEMA_INCOMPATIBLE,
        "Awesome state is incompatible with this version.",
        data={
            "found_schema": error.found,
            "expected_schema": error.expected,
            "state_directory": str(self._paths.state_dir.resolve()),
        },
    ) from error
```

Use `trust_status` for the existing trust branch. This check happens before
`_activate()` opens the checkpoint saver. Do not import Storage into
`facade.py`; `composition.py` is the concrete wiring boundary that may map the
adapter exception.

- [ ] **Step 6: Run the storage and Application tests**

```bash
uv run pytest tests/unit/storage/test_application_database.py tests/integration/test_workspace_trust.py tests/unit/application/test_facade.py -q
```

Expected: all tests pass; the mismatch is typed and non-retryable, and neither
Application nor checkpoint state is created or modified.

- [ ] **Step 7: Commit the Core boundary**

```bash
git add src/awesome_agent/storage/database.py src/awesome_agent/application/contracts.py src/awesome_agent/application/composition.py tests/unit/storage/test_application_database.py tests/integration/test_workspace_trust.py
git commit -m "fix: expose incompatible application state"
```

---

### Task 2: Make the product error contract exact across Python and TypeScript

**Files:**
- Modify: `scripts/generate_protocol_fixtures.py`
- Modify: `protocol/fixtures/v2/methods.valid.json`
- Modify: `protocol/fixtures/v2/results.failures.json`
- Modify: `protocol/fixtures/v2/manifest.json`
- Modify: `tui/src/protocol/base.ts`
- Test: `tests/unit/protocol/test_contract_fixtures.py`
- Test: `tui/tests/protocol/contracts.test.ts`
- Test: `tui/tests/contracts/fixtures.test.ts`

**Interfaces:**
- Consumes: `ProductErrorCode.STATE_SCHEMA_INCOMPATIBLE` from Task 1.
- Produces: a discriminated TypeScript `ProductError` variant with strict state-schema data.
- Produces fixture: `initialize.state_schema_incompatible`.

- [ ] **Step 1: Add a generated failure fixture and strict consumer assertions**

Add this generated valid method case immediately after `initialize.ready`:

```python
(
    "initialize.state_schema_incompatible",
    "initialize",
    {
        "protocol_version": 2,
        "client_name": "awesome",
        "client_version": PRODUCT_VERSION,
    },
    _model(
        ApplicationResult[InitializeResult].failure(
            ProductError(
                code=ProductErrorCode.STATE_SCHEMA_INCOMPATIBLE,
                message="Awesome state is incompatible with this version.",
                retryable=False,
                data={
                    "found_schema": 1,
                    "expected_schema": 2,
                    "state_directory": "C:\\Awesome\\state",
                },
            )
        )
    ),
),
```

In the TypeScript contract test, parse the case and assert the exact data. Also
assert parsing fails for missing `expected_schema`, a string schema number,
`retryable: true`, or an extra data field.

Update `_failure_results()` so the new enum member is emitted with the same
exact data instead of the generic empty data object. Keep every other failure
fixture unchanged. The shared fixture test must continue validating every
`ProductErrorCode` against `applicationResultSchema()`.

- [ ] **Step 2: Run fixture and TypeScript tests to verify the new code is rejected**

```bash
uv run python scripts/generate_protocol_fixtures.py
uv run pytest tests/unit/protocol/test_contract_fixtures.py -q
npm --prefix tui test -- tests/protocol/contracts.test.ts
npm --prefix tui test -- tests/contracts/fixtures.test.ts
```

Expected: Python generation succeeds, but TypeScript parsing fails until the
new code and strict variant exist.

- [ ] **Step 3: Add the strict discriminated error variant**

Keep generic error data for existing codes, but exclude the new code from that
branch. Define `genericProductErrorCodes` as the existing `as const` tuple and
derive the public inventory by appending the dedicated code; do not use
`Array.filter()` and cast the result back to a tuple:

```ts
const genericProductErrorCodes = [
  // every existing product error code, unchanged
] as const;

export const productErrorCodes = [
  ...genericProductErrorCodes,
  "state_schema_incompatible",
] as const;

export const stateSchemaIncompatibleDataSchema = z.strictObject({
  found_schema: safeIntegerSchema,
  expected_schema: safeIntegerSchema,
  state_directory: boundedText(1, 4_096),
});

const stateSchemaIncompatibleErrorSchema = z.strictObject({
  code: z.literal("state_schema_incompatible"),
  message: boundedText(1, 2_000),
  retryable: z.literal(false),
  data: stateSchemaIncompatibleDataSchema,
});

const genericProductErrorSchema = z.strictObject({
  code: z.enum(genericProductErrorCodes),
  message: boundedText(1, 2_000),
  retryable: z.boolean(),
  data: z.record(z.string(), jsonValueSchema),
});

export const productErrorSchema = z.discriminatedUnion("code", [
  genericProductErrorSchema,
  stateSchemaIncompatibleErrorSchema,
]);
```

`genericProductErrorSchema.code` must use an enum containing every existing
code except `state_schema_incompatible`. Do not weaken the state variant to a
generic record.

- [ ] **Step 4: Regenerate fixtures and run both producers and consumers**

```bash
uv run python scripts/generate_protocol_fixtures.py
uv run python scripts/generate_protocol_fixtures.py --check
uv run pytest tests/unit/protocol/test_contract_fixtures.py -q
npm --prefix tui test -- tests/protocol/contracts.test.ts
npm --prefix tui test -- tests/contracts/fixtures.test.ts
```

Expected: every command exits 0 and the manifest hashes match generated files.

- [ ] **Step 5: Commit the exact protocol contract**

```bash
git add scripts/generate_protocol_fixtures.py protocol/fixtures/v2 tui/src/protocol/base.ts tests/unit/protocol/test_contract_fixtures.py tui/tests/protocol/contracts.test.ts tui/tests/contracts/fixtures.test.ts
git commit -m "fix: type incompatible state across protocol"
```

---

### Task 3: Preserve product failure data through startup and render a Quit-only panel

**Files:**
- Modify: `tui/src/surface/startup.ts`
- Modify: `tui/src/lifecycle/fatal.ts`
- Modify: `tui/src/components/FatalScreen.tsx`
- Test: `tui/tests/lifecycle/fatal.test.ts`
- Test: `tui/tests/components/fatal-screen.test.tsx`
- Test: `tui/tests/cli/main.test.ts`

**Interfaces:**
- Produces: `StartupProductError` carrying `code`, `retryable`, and exact `data`.
- Produces Fatal variant: `{kind: "state_schema_incompatible", foundSchema, expectedSchema, stateDirectory}`.
- Consumes: strict `ProductError` from Task 2.

- [ ] **Step 1: Write startup classification and panel tests**

Add a lifecycle test:

```ts
const fatal = toFatalState(
  new StartupProductError({
    code: "state_schema_incompatible",
    message: "Awesome state is incompatible with this version.",
    retryable: false,
    data: {
      found_schema: 1,
      expected_schema: 2,
      state_directory: "E:\\awesome_agent\\.awesome-dev\\home\\state",
    },
  }),
  session,
);

expect(fatal).toEqual({
  kind: "state_schema_incompatible",
  foundSchema: 1,
  expectedSchema: 2,
  stateDirectory: "E:\\awesome_agent\\.awesome-dev\\home\\state",
});
```

Add a `FatalScreen` assertion that the panel contains found/expected versions,
the state directory, and `Quit`, while excluding `Reconnect`,
`core_request_failed`, raw traceback text, and automatic-reset wording.

Add a CLI test whose `initialize` result is the fixture failure and assert the
startup render receives this dedicated Fatal variant with exit code 1.

- [ ] **Step 2: Run the startup tests and verify they fail**

```bash
npm --prefix tui test -- tests/lifecycle/fatal.test.ts tests/components/fatal-screen.test.tsx tests/cli/main.test.ts
```

Expected: FAIL because `productFailure()` discards `retryable` and `data`, and
the dedicated Fatal variant does not exist.

- [ ] **Step 3: Preserve typed product failures**

Add a dedicated error class in `surface/startup.ts` without changing semantic
`StartupError`:

```ts
export class StartupProductError extends Error {
  readonly code: ProductError["code"];
  readonly retryable: boolean;
  readonly data: ProductError["data"];

  constructor(error: ProductError) {
    super(error.message);
    this.name = "StartupProductError";
    this.code = error.code;
    this.retryable = error.retryable;
    this.data = error.data;
  }
}

function productFailure(error: ProductError): StartupProductError {
  return new StartupProductError(error);
}
```

Do not attach product data to the general semantic `StartupError`.

- [ ] **Step 4: Classify the exact Fatal variant**

Extend `FatalState` and product-error classification:

```ts
| {
    readonly kind: "state_schema_incompatible";
    readonly foundSchema: number;
    readonly expectedSchema: number;
    readonly stateDirectory: string;
  }
```

When `isProductError(error)` and the code matches, validate the already-strict
data fields with `stateSchemaIncompatibleDataSchema.safeParse()` and return
this variant. If a malformed value somehow reaches this boundary, classify it
as a protocol Fatal with diagnostic code `invalid_state_schema_diagnostic`;
never guess missing versions or a path. Version incompatibility behavior
remains unchanged; unrelated product failures remain non-fatal where currently
expected.

- [ ] **Step 5: Render the dedicated startup panel**

In `FatalScreen`, branch before the generic startup diagnostic:

```tsx
{fatal.kind === "state_schema_incompatible" ? (
  <>
    <Text color={theme.danger}>
      Awesome state is incompatible with this version.
    </Text>
    <Text>
      Found schema {fatal.foundSchema} · Expected schema {fatal.expectedSchema}
    </Text>
    <Text>Close Awesome and reset this state directory:</Text>
    <Text>{fatal.stateDirectory}</Text>
  </>
) : startup ? (
  /* existing generic startup content */
) : (
  /* existing runtime fatal content */
)}
```

The Picker for this Fatal kind contains exactly one selected `Quit` option.
Do not route it through `executeFatalRecoverySelection()` or render Reconnect.

- [ ] **Step 6: Run the focused startup tests**

```bash
npm --prefix tui test -- tests/lifecycle/fatal.test.ts tests/components/fatal-screen.test.tsx tests/cli/main.test.ts tests/surface/startup.test.ts
```

Expected: all tests pass and the mismatch data is preserved without a raw
protocol error.

- [ ] **Step 7: Commit the startup UX**

```bash
git add tui/src/surface/startup.ts tui/src/lifecycle/fatal.ts tui/src/components/FatalScreen.tsx tui/tests/lifecycle/fatal.test.ts tui/tests/components/fatal-screen.test.tsx tui/tests/cli/main.test.ts
git commit -m "fix: explain incompatible state at startup"
```

---

### Task 4: Document scoped recovery and run cross-boundary validation

**Files:**
- Modify: `docs/getting-started/quickstart.md`
- Modify: `docs/getting-started/quickstart.zh-CN.md`
- Modify: `docs/user-guide/troubleshooting.md`
- Modify: `docs/architecture/storage.md`
- Test: `tests/integration/test_workspace_trust.py`
- Test: `tests/unit/protocol/test_contract_fixtures.py`
- Test: `tui/tests/components/fatal-screen.test.tsx`

**Interfaces:**
- Documents: stop process → inspect displayed path → delete only
  `<AWESOME_HOME>/state` → restart.
- Preserves: configuration and credential files outside `state`.

- [ ] **Step 1: Add user-facing recovery documentation**

The English and Chinese development Quickstarts must explain that source
checkouts can intentionally reject disposable state after a schema change.
Provide scoped commands only after instructing the developer to stop Awesome
and verify the path.

PowerShell:

```powershell
Resolve-Path .\.awesome-dev\home\state
Remove-Item -LiteralPath .\.awesome-dev\home\state -Recurse -Force
uv run awesome-dev
```

macOS/WSL2:

```bash
realpath .awesome-dev/home/state
rm -rf -- .awesome-dev/home/state
uv run awesome-dev
```

State explicitly that `config.yaml`, `ui.json`, and `<AWESOME_HOME>/.env` are
outside this reset. Do not tell installed users to delete all of
`AWESOME_HOME` and do not describe the reset as migration.

- [ ] **Step 2: Update architecture documentation**

Document:

```text
Storage detects incompatible schema
  -> Application maps state_schema_incompatible
  -> Protocol transports exact versions/path
  -> TUI renders Quit-only recovery
```

Keep the explicit no-migration policy and note that detection occurs before
writable database configuration.

- [ ] **Step 3: Run the complete validation ladder**

```bash
uv run ruff format --check src tests
uv run ruff check src tests
uv run mypy
uv run pytest tests/unit/storage/test_application_database.py tests/integration/test_workspace_trust.py tests/unit/application/test_facade.py tests/unit/protocol/test_contract_fixtures.py tests/unit/protocol/test_jsonrpc.py -q
uv run python scripts/generate_protocol_fixtures.py --check
npm --prefix tui run format:check
npm --prefix tui run lint
npm --prefix tui run typecheck
npm --prefix tui test -- tests/contracts/fixtures.test.ts tests/protocol/contracts.test.ts tests/surface/startup.test.ts tests/lifecycle/fatal.test.ts tests/components/fatal-screen.test.tsx tests/cli/main.test.ts
npm --prefix tui run build
git diff --check
rg -n "state_schema_incompatible|core_request_failed|ApplicationSchemaMismatch" src tui/src protocol tests docs
```

Expected: all checks pass. `state_schema_incompatible` appears at the defined
Storage/Application/Protocol/TUI boundaries; the known mismatch path contains
no `core_request_failed`, Reconnect, automatic delete, or Schema 1 read logic.

- [ ] **Step 4: Perform the source-startup acceptance check**

Create disposable isolated state with `PRAGMA user_version = 1`, run
`uv run awesome-dev` with `AWESOME_HOME` pointing at that isolated directory,
and verify the dedicated panel. Confirm the database bytes, complete
state-directory inventory, and sibling config remain unchanged. Then use a
fresh isolated home and verify startup reaches the Trust prompt.

Never run this check against the developer's default home and never delete the
fixture until its resolved path has been verified inside the repository's
ignored `.awesome-dev` directory.

- [ ] **Step 5: Commit documentation and open the PR**

```bash
git add docs/getting-started/quickstart.md docs/getting-started/quickstart.zh-CN.md docs/user-guide/troubleshooting.md docs/architecture/storage.md
git commit -m "docs: explain incompatible development state"
git status --short
```

Expected: clean worktree after the commit. Push the scoped branch, open a PR
against the integration branch after the Cursor PR has merged, include all
validation evidence, and merge only when conflict-free.
