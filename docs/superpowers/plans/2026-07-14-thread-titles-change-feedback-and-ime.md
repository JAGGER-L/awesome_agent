# Thread Titles, Change Feedback, Thinking Defaults, and IME Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace empty/internal Change output, add authoritative Thread titles and `/rename`, default new Threads to Thinking On, and anchor Windows IME composition to the real Ink Composer cursor.

**Architecture:** Core derives Change facts once from the Change Journal and transports a discriminated union to Ink; Ink only folds and colors those facts. Conversation storage owns title provenance and atomically applies the first-message title with Turn creation, while `/rename` is a deterministic Application command. Ink uses its built-in cursor and box metrics without adding another input owner.

**Tech Stack:** Python 3.12, Pydantic 2, SQLite, pytest, JSON-RPC/NDJSON Protocol v2, TypeScript 7, React 19, Ink 7.1, Zod 4, Vitest.

## Global Constraints

- Preserve every real Provider Thinking interval; do not merge, translate, filter, rewrite, or hide it.
- New Threads default to Thinking On; a resumed Thread retains its persisted setting.
- Automatic titles contain at most 48 visible grapheme clusters, including the final `…` when truncated.
- `/rename <title>` is mandatory-argument syntax; manual titles are at most 100 visible grapheme clusters and are rejected rather than truncated.
- `ToolSpec.read_only` is the only read/write classification authority.
- `/diff` and Change summaries must use one `ChangeAnalyzer` and one set of before/after bytes.
- Do not add a derived Change statistics table, another Runtime, another Event Store, a Git dependency, or a Windows-specific IME implementation.
- Do not add compatibility aliases, fallback payloads, legacy schema translators, or dual old/new presentation paths.
- Do not add production dependencies.
- Python producer and TypeScript consumer contracts remain strict and are validated by deterministic fixtures.
- The accepted Change UI is `◇ 1 file changed · Ctrl+O to expand`; expanded text rows show green `+N` and red `-N`, while no-color output keeps the symbols.

---

## Target File Structure

### New focused files

- `src/awesome_agent/core/changes/analysis.py`: merge recorded file mutations, load authoritative snapshots, classify text/binary/directory/symlink changes, and produce both Diff and structured deltas.
- `src/awesome_agent/application/change_scope.py`: own per-operation ChangeSet acquisition and use the Tool Registry's `read_only` fact.
- `src/awesome_agent/conversation/titles.py`: normalize, count, truncate, and validate automatic/manual Thread titles.
- `tui/src/components/transcript/ChangeSummary.tsx`: render the accepted folded/expanded Change presentation.
- `tui/src/components/use-composer-cursor.ts`: calculate and publish the real Ink cursor position without owning keyboard input.
- `tui/src/commands/effects.ts`: apply typed command effects to Surface state without mixing state mutation into Presenters.
- Focused tests beside the existing subsystem test suites; do not create a parallel test framework.

### Existing files with changed responsibility

- `src/awesome_agent/core/changes/operations.py`: delegate Diff generation to `ChangeAnalyzer`; keep Undo/Redo only.
- `src/awesome_agent/application/composition.py`: compose analyzer and change scope; stop allocating ChangeSets unconditionally.
- `src/awesome_agent/conversation/models.py` and `storage/database.py`: persist `ThreadTitleSource` and the new Thinking default in the single current schema.
- `src/awesome_agent/conversation/service.py` and `storage/conversations.py`: atomically create the first entry, Turn, and automatic title.
- `src/awesome_agent/application/command_results.py`: expose structured Change facts and `ThreadRenamedPayload`.
- `tui/src/state/`, `tui/src/transcript/`, and `tui/src/protocol/`: consume exact facts and remove the unused `workspace.changed/latest_change` path.
- `tui/src/components/Composer.tsx`: render text without a fake block cursor and delegate cursor placement to the focused hook.

## Ordered Task / PR Boundaries

Each task below is one reviewable PR. Merge it into the integration branch before starting the next task so later tasks consume only merged interfaces.

### Task 1: Introduce the single Change Analyzer

**Files:**
- Create: `src/awesome_agent/core/changes/analysis.py`
- Modify: `src/awesome_agent/core/changes/operations.py`
- Modify: `src/awesome_agent/core/changes/__init__.py`
- Create: `tests/unit/core/changes/test_analysis.py`
- Test: `tests/unit/core/changes/test_operations.py`

**Interfaces:**
- Produces: `TextFileChange`, `BinaryFileChange`, `DirectoryChange`, `SymlinkChange`, `ChangeDelta`, `ChangeAnalysis`, and `ChangeAnalyzer.analyze(change_set_id: str) -> ChangeAnalysis`.
- Produces: `merge_file_changes(changes: list[FileChange]) -> tuple[FileChange, ...]` for Undo/Redo and analysis.
- Changes: `ChangeOperations.__init__(..., analyzer: ChangeAnalyzer | None = None)` and `diff()` delegate to the analyzer.

- [ ] **Step 1: Write analyzer tests that cover every existing node type and shared Diff facts**

```python
def test_analysis_returns_text_counts_and_the_same_unified_diff(tmp_path: Path) -> None:
    journal, store, blobs, workspace = analysis_fixture(tmp_path)
    path = workspace / "area.py"
    path.write_text("def area(r):\n    return 0\n", encoding="utf-8")
    change_set = record_file_update(
        journal,
        workspace,
        path,
        b"def area(r):\n    return 3.14 * r * r\n",
    )

    analysis = ChangeAnalyzer(store, blobs, resolve_workspace(workspace)).analyze(
        change_set.id
    )

    assert analysis.changes == (
        TextFileChange(
            path="area.py",
            change_kind=FileChangeKind.UPDATED,
            additions=1,
            deletions=1,
        ),
    )
    assert "-    return 0" in analysis.diff
    assert "+    return 3.14 * r * r" in analysis.diff


def test_analysis_classifies_binary_directory_and_symlink(tmp_path: Path) -> None:
    analysis = build_mixed_analysis(tmp_path)
    assert [change.kind for change in analysis.changes] == [
        "binary_file",
        "directory",
        "symlink",
    ]
```

- [ ] **Step 2: Run the focused tests and verify they fail before the analyzer exists**

Run:

```bash
uv run pytest tests/unit/core/changes/test_analysis.py tests/unit/core/changes/test_operations.py -q
```

Expected: collection fails because `awesome_agent.core.changes.analysis` and the delta models do not exist.

- [ ] **Step 3: Implement immutable structured deltas and one analysis result**

```python
class TextFileChange(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["text_file"] = "text_file"
    path: str = Field(min_length=1, max_length=1_000)
    change_kind: FileChangeKind
    additions: int = Field(ge=0)
    deletions: int = Field(ge=0)


class BinaryFileChange(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["binary_file"] = "binary_file"
    path: str = Field(min_length=1, max_length=1_000)
    change_kind: FileChangeKind
    before_bytes: int = Field(ge=0)
    after_bytes: int = Field(ge=0)


class DirectoryChange(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["directory"] = "directory"
    path: str = Field(min_length=1, max_length=1_000)
    change_kind: FileChangeKind


class SymlinkChange(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["symlink"] = "symlink"
    path: str = Field(min_length=1, max_length=1_000)
    change_kind: FileChangeKind


ChangeDelta = Annotated[
    TextFileChange | BinaryFileChange | DirectoryChange | SymlinkChange,
    Field(discriminator="kind"),
]


class ChangeAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    diff: str = Field(default="", max_length=MAX_DIFF_CHARS)
    changes: tuple[ChangeDelta, ...] = Field(default=(), max_length=1_000)
```

`ChangeAnalyzer.analyze()` must sort by path, classify NUL-containing or non-UTF-8 file bytes as binary, use `difflib.SequenceMatcher` opcodes for additions/deletions, and use those same decoded line arrays for `difflib.unified_diff`. Missing referenced blobs raise the existing Change invariant error rather than becoming empty bytes.

- [ ] **Step 4: Make `ChangeOperations.diff()` delegate and remove its duplicate Diff/classification code**

```python
class ChangeOperations:
    def __init__(
        self,
        store: ChangeSetStore,
        blobs: ChangeBlobStore,
        workspace: WorkspaceIdentity,
        analyzer: ChangeAnalyzer | None = None,
    ) -> None:
        self._store = store
        self._blobs = blobs
        self._workspace = workspace
        self._analyzer = analyzer or ChangeAnalyzer(store, blobs, workspace)

    def diff(self, change_set_id: str) -> str:
        return self._analyzer.analyze(change_set_id).diff
```

Move `_merge_changes` to `merge_file_changes()` in `analysis.py` and call it from both analysis and Undo/Redo. Delete the former analyzer-like helpers from `operations.py`.

- [ ] **Step 5: Run focused quality gates**

```bash
uv run ruff format --check src/awesome_agent/core/changes tests/unit/core/changes
uv run ruff check src/awesome_agent/core/changes tests/unit/core/changes
uv run mypy src/awesome_agent/core/changes tests/unit/core/changes
uv run pytest tests/unit/core/changes -q
```

Expected: all commands pass; existing Undo/Redo behavior remains unchanged.

- [ ] **Step 6: Commit Task 1**

```bash
git add src/awesome_agent/core/changes tests/unit/core/changes
git commit -m "refactor: centralize change analysis"
```

### Task 2: Allocate ChangeSets only from Tool metadata

**Files:**
- Create: `src/awesome_agent/application/change_scope.py`
- Modify: `src/awesome_agent/application/composition.py`
- Modify: `src/awesome_agent/agent/context.py`
- Modify: `src/awesome_agent/agent/nodes.py`
- Create: `tests/unit/application/test_change_scope.py`
- Test: `tests/unit/agent/test_graph.py`
- Create: `tests/integration/test_application_tools.py`

**Interfaces:**
- Consumes: `ToolRegistry.resolve(name)` and `RegisteredTool.spec.read_only`.
- Produces: `ChangeScope.change_set_for_tool(tool_name: str, owner: str, turn_id: str | None) -> str | None`.
- Changes: `AgentRuntimeContext.tool_context_factory` receives both `AgentState` and the exact `ToolRequest`.

- [ ] **Step 1: Write tests proving read tools cannot allocate a ChangeSet**

```python
def test_read_only_tool_does_not_allocate_change_set(scope_fixture: ScopeFixture) -> None:
    scope = scope_fixture.scope
    assert scope.change_set_for_tool(
        tool_name="read_file", owner="turn_1", turn_id="turn_1"
    ) is None
    assert scope_fixture.store.latest(scope_fixture.workspace.key) is None


def test_write_tool_reuses_one_owner_change_set(scope_fixture: ScopeFixture) -> None:
    first = scope_fixture.scope.change_set_for_tool(
        tool_name="write_file", owner="turn_1", turn_id="turn_1"
    )
    second = scope_fixture.scope.change_set_for_tool(
        tool_name="edit_file", owner="turn_1", turn_id="turn_1"
    )
    assert first is not None
    assert second == first
```

- [ ] **Step 2: Run tests and verify the unconditional allocation path fails them**

```bash
uv run pytest tests/unit/application/test_change_scope.py tests/integration/test_application_tools.py -q
```

Expected: failure because `_ChangeScope` is private to composition and every tool context currently calls `acquire()`.

- [ ] **Step 3: Move scope ownership into a focused Application module**

```python
class ChangeScope:
    def __init__(
        self,
        *,
        journal: ChangeJournal,
        store: ChangeSetStore,
        registry: ToolRegistry,
        session_id: str,
        workspace: WorkspaceIdentity,
    ) -> None:
        self._journal = journal
        self._store = store
        self._registry = registry
        self._session_id = session_id
        self._workspace = workspace
        self._identifiers: dict[str, str] = {}

    def change_set_for_tool(
        self,
        *,
        tool_name: str,
        owner: str,
        turn_id: str | None,
    ) -> str | None:
        registered = self._registry.resolve(tool_name)
        if registered is None or registered.spec.read_only:
            return None
        return self.acquire(owner, turn_id=turn_id)
```

Move `acquire`, `seal`, and `reconcile` without semantic changes, then delete `_ChangeScope` from `composition.py`.

- [ ] **Step 4: Pass the exact request to the context factory**

```diff
-    tool_context_factory: Callable[[AgentState], ToolExecutionContext]
+    tool_context_factory: Callable[[AgentState, ToolRequest], ToolExecutionContext]
```

```python
request = ToolRequest(
    call_id=call_id,
    tool_name=tool_name,
    arguments=cast(dict[str, JsonValue], arguments),
)
result = await context.executor.execute(
    request,
    context=context.tool_context_factory(updated, request),
)
```

In composition, resolve `request.tool_name` through `ChangeScope.change_set_for_tool()`. Direct `! shell` passes the canonical tool name `execute` through the same method. Do not add a second read-only-name set.

- [ ] **Step 5: Verify read-only, mutating, direct execute, unknown tool, and cancellation paths**

```bash
uv run ruff check src/awesome_agent/application/change_scope.py src/awesome_agent/application/composition.py src/awesome_agent/agent tests/unit/application/test_change_scope.py tests/unit/agent/test_graph.py tests/integration/test_application_tools.py
uv run mypy src/awesome_agent/application src/awesome_agent/agent tests/unit/application/test_change_scope.py tests/unit/agent/test_graph.py
uv run pytest tests/unit/application/test_change_scope.py tests/unit/agent/test_graph.py tests/integration/test_application_tools.py -q
```

Expected: read-only tools have `change_set_id=None`; writes have a stable ID; execute-only audit may have a ChangeSet but no file deltas.

- [ ] **Step 6: Commit Task 2**

```bash
git add src/awesome_agent/application/change_scope.py src/awesome_agent/application/composition.py src/awesome_agent/agent tests/unit/application/test_change_scope.py tests/unit/agent/test_graph.py tests/integration/test_application_tools.py
git commit -m "fix: allocate changes only for mutating tools"
```

### Task 3: Replace legacy Change protocol shapes

**Files:**
- Modify: `src/awesome_agent/application/contracts.py`
- Modify: `src/awesome_agent/application/composition.py`
- Modify: `src/awesome_agent/core/events.py`
- Modify: `tui/src/protocol/product-projections.ts`
- Modify: `tui/src/protocol/events.ts`
- Modify: `tui/src/state/model.ts`
- Modify: `tui/src/state/reducer.ts`
- Modify: `protocol/fixtures/v2/application.json`
- Modify: `protocol/fixtures/v2/events.json`
- Test: `tests/unit/protocol/test_contract_fixtures.py`
- Test: `tui/tests/protocol/contracts.test.ts`
- Modify: `tests/structural/test_product_architecture.py`

**Interfaces:**
- Consumes: `ChangeAnalyzer.analyze()` from Task 1.
- Produces: `ChangeSetSummary.changes: tuple[ChangeDelta, ...]` across Python and Zod.
- Removes: unused `workspace.changed` event, `WorkspaceChangedPayload`, Surface `latest_change`, `changed_paths`, and protocol `reversibility` presentation fields.

- [ ] **Step 1: Add producer-consumer tests for all four delta variants and an empty execute-only summary**

```python
def test_thread_read_fixture_contains_discriminated_change_deltas() -> None:
    payload = load_fixture("application.json", "thread.read.with_changes")
    result = ThreadReadResult.model_validate(payload)
    assert [change.kind for change in result.change_sets[0].changes] == [
        "text_file",
        "binary_file",
        "directory",
        "symlink",
    ]
```

```ts
expect(
  methodSchemas["thread.read"].result.parse(fixture).change_sets[0]?.changes,
).toEqual([
  expect.objectContaining({ kind: "text_file", additions: 16, deletions: 2 }),
  expect.objectContaining({ kind: "binary_file" }),
  expect.objectContaining({ kind: "directory" }),
  expect.objectContaining({ kind: "symlink" }),
]);
```

- [ ] **Step 2: Run contract tests and verify the old shape fails**

```bash
uv run pytest tests/unit/protocol/test_contract_fixtures.py -q
npm --prefix tui test -- tests/protocol/contracts.test.ts
```

Expected: fixtures fail until Python and TypeScript both use `changes`.

- [ ] **Step 3: Replace the Application summary producer**

```python
class ChangeSetSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    change_set_id: str = Field(min_length=1, max_length=128)
    turn_id: str | None = Field(default=None, max_length=128)
    operation_id: str | None = Field(default=None, max_length=128)
    lifecycle: str = Field(min_length=1, max_length=64)
    changes: tuple[ChangeDelta, ...] = Field(default=(), max_length=1_000)
    created_at: datetime
    sealed_at: datetime | None = None
```

`_page_change_summaries()` calls the composed analyzer once per ChangeSet and skips summaries whose `changes` tuple is empty. It must not catch a missing-blob invariant and fabricate an empty summary.

- [ ] **Step 4: Replace the Zod schema exactly**

```ts
const changeDeltaSchema = z.discriminatedUnion("kind", [
  z.strictObject({
    kind: z.literal("text_file"),
    path: boundedText(1, 1_000),
    change_kind: z.enum(["created", "updated", "deleted"]),
    additions: safeIntegerSchema.min(0),
    deletions: safeIntegerSchema.min(0),
  }),
  z.strictObject({
    kind: z.literal("binary_file"),
    path: boundedText(1, 1_000),
    change_kind: z.enum(["created", "updated", "deleted"]),
    before_bytes: safeIntegerSchema.min(0),
    after_bytes: safeIntegerSchema.min(0),
  }),
  z.strictObject({
    kind: z.literal("directory"),
    path: boundedText(1, 1_000),
    change_kind: z.enum(["created", "updated", "deleted"]),
  }),
  z.strictObject({
    kind: z.literal("symlink"),
    path: boundedText(1, 1_000),
    change_kind: z.enum(["created", "updated", "deleted"]),
  }),
]);
```

- [ ] **Step 5: Delete the unproduced event and duplicate live state path**

Delete `EventType.WORKSPACE_CHANGED`, `WorkspaceChangedPayload`, the corresponding Zod event branch, `SurfaceState.latest_change`, reducer handling, and live transcript projection. Durable `thread.read` reconciliation becomes the only Change block source.

- [ ] **Step 6: Add a structural assertion that legacy fields cannot return**

```python
def test_change_presentation_has_no_legacy_wire_fields() -> None:
    forbidden = ("changed_paths", "reversibility", "workspace.changed")
    roots = (PROJECT_ROOT / "tui" / "src", PROJECT_ROOT / "protocol" / "fixtures")
    files = (
        path
        for root in roots
        for pattern in ("*.ts", "*.tsx", "*.json")
        for path in root.rglob(pattern)
    )
    text = "\n".join(path.read_text("utf-8") for path in files)
    for token in forbidden:
        assert token not in text
```

The structural test targets protocol/presentation files only; domain-level Change reversibility remains required for Undo/Redo.

- [ ] **Step 7: Run contracts, types, and focused state tests**

```bash
uv run pytest tests/unit/protocol/test_contract_fixtures.py tests/structural/test_product_architecture.py -q
npm --prefix tui run typecheck
npm --prefix tui test -- tests/protocol/contracts.test.ts tests/state/reducer.test.ts tests/transcript/live.test.ts
```

Expected: all pass and no unused workspace-change path remains.

- [ ] **Step 8: Commit Task 3**

```bash
git add src/awesome_agent/application/contracts.py src/awesome_agent/application/composition.py src/awesome_agent/core/events.py tui/src/protocol tui/src/state tui/src/transcript/live.ts protocol/fixtures/v2 tests/unit/protocol tests/structural/test_product_architecture.py
git commit -m "refactor: transport structured workspace changes"
```

### Task 4: Implement the accepted folded Change UI

**Files:**
- Create: `tui/src/components/transcript/ChangeSummary.tsx`
- Modify: `tui/src/components/transcript/blocks/BlockView.tsx`
- Modify: `tui/src/transcript/model.ts`
- Modify: `tui/src/transcript/hydrate.ts`
- Modify: `tui/src/transcript/reconcile.ts`
- Create: `tui/tests/components/change-summary.test.tsx`
- Test: `tui/tests/transcript/hydrate.test.ts`
- Test: `tui/tests/transcript/reconcile.test.ts`

**Interfaces:**
- Consumes: Task 3's exact `changes` discriminated union.
- Produces: `ChangeSummary` with folded/expanded rendering controlled only by the existing global `detailsExpanded` state.

- [ ] **Step 1: Write exact visual behavior tests before changing the renderer**

```tsx
it("folds a text change with the accepted summary", () => {
  const view = renderWithTheme(
    <ChangeSummary
      expanded={false}
      changes={[
        {
          kind: "text_file",
          path: "src/main.py",
          change_kind: "updated",
          additions: 16,
          deletions: 2,
        },
      ]}
    />,
  );
  expect(view.lastFrame()).toContain("◇ 1 file changed · Ctrl+O to expand");
  expect(view.lastFrame()).not.toContain("src/main.py");
});

it("expands aligned rows with accessible git-style signs", () => {
  const frame = renderExpandedChanges().lastFrame();
  expect(frame).toContain("src/main.py");
  expect(frame).toContain("+16");
  expect(frame).toContain("-2");
  expect(frame).toContain("Binary 12 → 20 bytes");
  expect(frame).toContain("Directory created");
  expect(frame).toContain("Symlink updated");
});
```

Also assert ANSI color output uses `theme.success` for `+N` and `theme.danger` for `-N`, and a no-color theme retains both signs.

- [ ] **Step 2: Run focused TUI tests and verify the raw `Changed · full` output fails**

```bash
npm --prefix tui test -- tests/components/change-summary.test.tsx tests/transcript/hydrate.test.ts tests/transcript/reconcile.test.ts
```

Expected: failure because `ChangeSummary` does not exist and `BlockView` still renders paths plus reversibility.

- [ ] **Step 3: Change the transcript model to structured facts**

```ts
type ChangeDelta = MethodValue["thread.read"]["change_sets"][number]["changes"][number];

export interface ChangeSummaryBlock extends BlockBase {
  readonly kind: "change";
  readonly change_set_id: string;
  readonly lifecycle: string;
  readonly changes: readonly ChangeDelta[];
}
```

Hydration and reconciliation copy `change.changes` without conversion and skip an empty array defensively as an invariant guard.

- [ ] **Step 4: Implement one renderer with exact copy and semantic colors**

```tsx
export function ChangeSummary({ changes, expanded }: Props) {
  const theme = useTheme();
  return (
    <ExpandableDetails
      expanded={expanded}
      summary={<Text color={theme.secondary}>◇ {formatChangeCount(changes)} changed</Text>}
    >
      <Box flexDirection="column">
        {changes.map((change) => (
          <ChangeRow key={`${change.kind}:${change.path}`} change={change} />
        ))}
      </Box>
    </ExpandableDetails>
  );
}
```

`formatChangeCount()` groups text and binary as files, then directories and symlinks, and applies singular/plural grammar. `ChangeRow` pads the path column once for the group. Text rows render separate nested `<Text color={theme.success}>+N</Text>` and `<Text color={theme.danger}>-N</Text>` nodes.

- [ ] **Step 5: Delete the raw BlockView branch and route through `ChangeSummary`**

```tsx
case "change":
  return (
    <ChangeSummary changes={block.changes} expanded={detailsExpanded} width={width} />
  );
```

There must be no renderer for `paths` or `reversibility` after this step.

- [ ] **Step 6: Run TUI formatting, lint, type, and focused rendering tests**

```bash
npm --prefix tui run format:check
npm --prefix tui run lint
npm --prefix tui run typecheck
npm --prefix tui test -- tests/components/change-summary.test.tsx tests/transcript/hydrate.test.ts tests/transcript/reconcile.test.ts tests/components/transcript.test.tsx
```

Expected: folded and expanded outputs match the accepted UI and all tests pass.

- [ ] **Step 7: Commit Task 4**

```bash
git add tui/src/components/transcript tui/src/transcript tui/tests/components/change-summary.test.tsx tui/tests/transcript
git commit -m "feat: render structured change summaries"
```

### Task 5: Persist title provenance and make Thinking On the new-Thread default

**Files:**
- Modify: `src/awesome_agent/conversation/models.py`
- Modify: `src/awesome_agent/conversation/service.py`
- Modify: `src/awesome_agent/storage/database.py`
- Modify: `src/awesome_agent/storage/conversations.py`
- Modify: `src/awesome_agent/config/models.py`
- Modify: `src/awesome_agent/config/resolver.py`
- Modify: `src/awesome_agent/application/contracts.py`
- Modify: `src/awesome_agent/application/composition.py`
- Modify: `tui/src/protocol/product-projections.ts`
- Modify: affected deterministic fixtures
- Test: `tests/unit/conversation/test_models.py`
- Test: `tests/unit/conversation/test_service.py`
- Test: `tests/unit/storage/test_conversation_storage.py`
- Test: `tests/unit/config/test_config_resolver.py`

**Interfaces:**
- Produces: `ThreadTitleSource.AUTOMATIC | MANUAL` and `Thread.title_source`.
- Changes: `Thread.thinking_enabled` and new-Thread resolver fallbacks default to `True`.
- Schema: bump `APPLICATION_SCHEMA_VERSION` from 1 to 2; no v1 migration or compatibility adapter.

- [ ] **Step 1: Write model, storage, and resolver tests for the new authority**

```python
def test_new_thread_defaults_to_automatic_title_and_thinking_on(store) -> None:
    thread = ConversationService(store=store).create_thread("workspace_1")
    assert thread.title == "New conversation"
    assert thread.title_source is ThreadTitleSource.AUTOMATIC
    assert thread.thinking_enabled is True


def test_stored_thinking_off_wins_when_thread_is_resumed(config_sources, thread) -> None:
    selected = resolve_turn_config(
        config_sources,
        thread.model_copy(update={"thinking_enabled": False}),
    )
    assert selected.thinking_enabled is False
```

Add a database test asserting schema version 2 and exact `title_source` round-trip.

- [ ] **Step 2: Run focused tests and verify current defaults/schema fail**

```bash
uv run pytest tests/unit/conversation/test_models.py tests/unit/conversation/test_service.py tests/unit/storage/test_conversation_storage.py tests/unit/config/test_config_resolver.py -q
```

Expected: failures show the missing provenance field and current `False` defaults.

- [ ] **Step 3: Add the current Thread model and schema**

```python
class ThreadTitleSource(StrEnum):
    AUTOMATIC = "automatic"
    MANUAL = "manual"


title: str = Field(min_length=1, max_length=500)
title_source: ThreadTitleSource = ThreadTitleSource.AUTOMATIC
thinking_enabled: bool = True
```

The three declarations replace the corresponding fields inside the existing
`Thread` model; all other declared Thread fields keep their current definitions.

```sql
CREATE TABLE threads (
    thread_id TEXT PRIMARY KEY,
    workspace_key TEXT NOT NULL,
    title TEXT NOT NULL,
    title_source TEXT NOT NULL CHECK (title_source IN ('automatic', 'manual')),
    current_model TEXT,
    thinking_enabled INTEGER NOT NULL CHECK (thinking_enabled IN (0, 1)),
    skill_mode TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

Set `APPLICATION_SCHEMA_VERSION = 2`. Preserve the repository's explicit schema-mismatch failure; do not add `ALTER TABLE`, v1 inference, or a default-on-read fallback.

- [ ] **Step 4: Update only user-Turn Thinking defaults**

Set the Thread, `TurnConfig`, Application current-thread fallback, and protocol fixture defaults to On. Keep internal Memory distillation and compression model calls explicitly Off where they already opt out; those are not user Thread defaults.

- [ ] **Step 5: Run schema, model, configuration, and protocol checks**

```bash
uv run ruff check src/awesome_agent/conversation src/awesome_agent/storage/database.py src/awesome_agent/storage/conversations.py src/awesome_agent/config tests/unit/conversation tests/unit/storage/test_conversation_storage.py tests/unit/config
uv run mypy src/awesome_agent/conversation src/awesome_agent/storage src/awesome_agent/config tests/unit/conversation tests/unit/storage/test_conversation_storage.py tests/unit/config
uv run pytest tests/unit/conversation tests/unit/storage/test_conversation_storage.py tests/unit/config -q
uv run pytest tests/unit/protocol/test_contract_fixtures.py -q
npm --prefix tui run typecheck
```

Expected: fresh schema data round-trips provenance and new Threads resolve Thinking On.

- [ ] **Step 6: Commit Task 5**

```bash
git add src/awesome_agent/conversation src/awesome_agent/storage/database.py src/awesome_agent/storage/conversations.py src/awesome_agent/config src/awesome_agent/application/contracts.py src/awesome_agent/application/composition.py tui/src/protocol protocol/fixtures tests/unit/conversation tests/unit/storage tests/unit/config
git commit -m "feat: persist thread title provenance"
```

### Task 6: Name a Thread atomically from its first accepted message

**Files:**
- Create: `src/awesome_agent/conversation/titles.py`
- Modify: `src/awesome_agent/conversation/repository.py`
- Modify: `src/awesome_agent/conversation/service.py`
- Modify: `src/awesome_agent/storage/conversations.py`
- Modify: `tui/src/surface/controller.ts`
- Modify: `tui/src/state/actions.ts`
- Modify: `tui/src/state/reducer.ts`
- Create: `tests/unit/conversation/test_titles.py`
- Test: `tests/unit/conversation/test_service.py`
- Test: `tests/unit/storage/test_conversation_storage.py`
- Create: `tui/tests/surface/reconciliation.test.ts`

**Interfaces:**
- Produces: `normalize_title(text: str) -> str`, `automatic_title(text: str) -> str`, and `visible_graphemes(text: str) -> tuple[str, ...]` using standard-library Unicode data.
- Changes: `ConversationStore.begin_turn(user_entry, turn, updated_thread) -> Turn` commits all three facts in one SQLite transaction.
- Changes: terminal reconciliation carries the authoritative `thread.read` projection into Surface state.

- [ ] **Step 1: Write Unicode, queue-boundary, cancellation, and atomicity tests**

```python
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  fix   the tests  ", "fix the tests"),
        ("你\u0301 好", "你\u0301 好"),
        ("👩‍💻" * 49, "👩‍💻" * 47 + "…"),
    ],
)
def test_automatic_title_is_normalized_and_bounded(raw: str, expected: str) -> None:
    assert automatic_title(raw) == expected


def test_begin_first_turn_updates_title_and_entry_atomically(service, store) -> None:
    thread = service.create_thread("workspace_1")
    service.begin_turn(thread.id, "  calculate   cube  ", turn_config(), client_message_id="client_1")
    view = store.read_thread(thread.id)
    assert view.thread.title == "calculate cube"
    assert view.thread.title_source is ThreadTitleSource.AUTOMATIC
    assert len(view.entries) == 1
    assert len(view.turns) == 1
```

Inject a failing Turn repository into the storage test and assert title and user entry are both rolled back.

- [ ] **Step 2: Run focused tests and verify title remains `New conversation`**

```bash
uv run pytest tests/unit/conversation/test_titles.py tests/unit/conversation/test_service.py tests/unit/storage/test_conversation_storage.py -q
```

Expected: failure until the title helper and atomic repository signature exist.

- [ ] **Step 3: Implement deterministic title normalization without a dependency**

```python
def normalize_title(value: str) -> str:
    return " ".join(value.split())


def automatic_title(value: str) -> str:
    normalized = normalize_title(value)
    clusters = visible_graphemes(normalized)
    if len(clusters) <= 48:
        return normalized
    return "".join(clusters[:47]) + "…"
```

`visible_graphemes()` groups combining marks, variation selectors, emoji modifiers, regional-indicator pairs, and zero-width-joiner sequences. Tests define the supported deterministic behavior; do not add `regex`, `wcwidth`, or another package.

- [ ] **Step 4: Make first-entry creation one transaction**

```python
def begin_turn(
    self,
    user_entry: ThreadEntry,
    turn: Turn,
    updated_thread: Thread,
) -> Turn:
    with self.transaction() as connection:
        current = self.threads.get(turn.thread_id, connection=connection)
        if current is None:
            raise ThreadNotFound(turn.thread_id)
        if updated_thread.id != current.id:
            raise ConversationConflict("Thread update identity differs.")
        if self.turns.in_progress(turn.thread_id, connection=connection):
            raise TurnBusy(turn.thread_id)
        self._require_next_sequence(user_entry, connection)
        self.entries.append(user_entry, connection=connection)
        self.turns.create(turn, connection=connection)
        self.threads.update(updated_thread, connection=connection)
    return turn
```

The service applies `automatic_title(user_content)` only when the Thread source is automatic and the view has no entries. Once `begin_turn()` commits, later model/tool failure or cancellation does not revert it. Pending TUI queue items do not call this path until promoted.

- [ ] **Step 5: Reconcile authoritative Thread metadata with terminal blocks**

```ts
store.dispatch({
  type: "transcript.reconciled",
  generation,
  operation_id: result.operation_id,
  turn_id: result.turn_id,
  blocks: result.blocks,
  thread: page.ok ? page.value : undefined,
});
```

The reducer replaces `state.thread` only when the generation and Thread identity match. Do not derive a title from a `UserBlock` in Ink.

- [ ] **Step 6: Run atomic storage and Surface reconciliation tests**

```bash
uv run ruff check src/awesome_agent/conversation src/awesome_agent/storage/conversations.py tests/unit/conversation tests/unit/storage/test_conversation_storage.py
uv run mypy src/awesome_agent/conversation src/awesome_agent/storage/conversations.py tests/unit/conversation tests/unit/storage/test_conversation_storage.py
uv run pytest tests/unit/conversation tests/unit/storage/test_conversation_storage.py -q
npm --prefix tui run typecheck
npm --prefix tui test -- tests/surface/reconciliation.test.ts tests/state/reducer.test.ts
```

Expected: title, entry, and Turn are atomic; terminal completion refreshes the title exactly once.

- [ ] **Step 7: Commit Task 6**

```bash
git add src/awesome_agent/conversation src/awesome_agent/storage/conversations.py tui/src/surface/controller.ts tui/src/state tests/unit/conversation tests/unit/storage/test_conversation_storage.py tui/tests/surface tui/tests/state
git commit -m "feat: name threads from first messages"
```

### Task 7: Add deterministic `/rename` and remove hidden `/new <title>`

**Files:**
- Modify: `src/awesome_agent/application/commands.py`
- Modify: `src/awesome_agent/application/conversation_commands.py`
- Modify: `src/awesome_agent/application/command_results.py`
- Modify: `src/awesome_agent/application/composition.py`
- Modify: `src/awesome_agent/conversation/service.py`
- Modify: `tui/src/protocol/commands.ts`
- Modify: `tui/src/commands/catalog.ts`
- Create: `tui/src/commands/effects.ts`
- Modify: `tui/src/commands/presenters.ts`
- Modify: `tui/src/app/App.tsx`
- Modify: `tui/src/state/actions.ts`
- Modify: `tui/src/state/reducer.ts`
- Modify: `protocol/fixtures/v2/commands.json`
- Test: `tests/unit/application/test_conversation_commands.py`
- Test: `tests/unit/conversation/test_service.py`
- Test: `tui/tests/commands/catalog-presenters.test.tsx`
- Test: `tui/tests/components/app-command-flow.test.tsx`
- Test: `tui/tests/pending-input/app-flow.test.tsx`

**Interfaces:**
- Produces: `CommandName.RENAME`, `ThreadRenamedPayload(kind="thread_renamed", thread=Thread)`.
- Produces: `ConversationService.rename_thread(thread_id: str, title: str) -> Thread`.
- Removes: title argument handling from `/new`; no alias or deprecation branch.

- [ ] **Step 1: Write Core command tests for success and every required failure**

```python
async def test_rename_updates_title_and_marks_it_manual(service, conversation) -> None:
    outcome = await service.rename(
        CommandIntent(name=CommandName.RENAME, arguments=("My", "session"))
    )
    assert outcome.kind == "result"
    assert outcome.payload.kind == "thread_renamed"
    assert outcome.payload.thread.title == "My session"
    assert outcome.payload.thread.title_source is ThreadTitleSource.MANUAL


@pytest.mark.parametrize("arguments", [(), ("   ",)])
async def test_rename_requires_a_title(service, arguments) -> None:
    outcome = await service.rename(
        CommandIntent(name=CommandName.RENAME, arguments=arguments)
    )
    assert outcome.kind == "error"
    assert outcome.code == "invalid_arguments"
    assert outcome.message == "Title required · /rename <title>"
```

Add an over-100-grapheme rejection test and a `/new unexpected` rejection test.

- [ ] **Step 2: Run focused command tests and verify rename is unregistered**

```bash
uv run pytest tests/unit/application/test_conversation_commands.py tests/unit/conversation/test_service.py -q
npm --prefix tui test -- tests/commands/catalog-presenters.test.tsx
```

Expected: Python lacks `CommandName.RENAME`; TUI catalog lacks `/rename`.

- [ ] **Step 3: Implement the deterministic service and typed payload**

```python
class ThreadRenamedPayload(_CommandModel):
    kind: Literal["thread_renamed"] = "thread_renamed"
    thread: Thread


def rename_thread(self, thread_id: str, title: str) -> Thread:
    normalized = normalize_title(title)
    if not normalized:
        raise ValueError("Title required · /rename <title>")
    if len(visible_graphemes(normalized)) > 100:
        raise ValueError("Thread title must be 100 characters or fewer.")
    current = self._store.read_thread(thread_id).thread
    return self._store.update_thread(
        current.model_copy(
            update={
                "title": normalized,
                "title_source": ThreadTitleSource.MANUAL,
                "updated_at": self._clock(),
            }
        )
    )
```

The Application service maps known validation failures to `invalid_arguments` and missing current Thread to `thread_not_found`. It returns success only after storage succeeds.

- [ ] **Step 4: Register one canonical command and reject `/new` arguments**

```python
class CommandName(StrEnum):
    RENAME = "rename"
```

Add this member to the existing enum without changing the other canonical names.

```ts
rename: entry(
  "rename",
  "/rename <title>",
  "Rename the current conversation",
),
```

Autocomplete remains `/rename`, never `/rename <title>`. Remove the current title extraction from `ConversationCommandService.new()` and return `invalid_arguments` for any argument.

- [ ] **Step 5: Apply the semantic rename effect separately from presentation**

Add a Surface action carrying the authoritative Thread. A dedicated effect
function applies semantic state before the same payload is passed to the
Presenter:

```ts
export function applyCommandEffect(
  payload: CommandPayload,
  store: Pick<SurfaceStore, "dispatch">,
): void {
  if (payload.kind === "thread_renamed") {
    store.dispatch({ type: "thread.metadata.updated", thread: payload.thread });
  }
}
```

The Presenter remains pure:

```ts
case "thread_renamed":
  return {
    kind: "notice",
    message: `Conversation renamed · ${payload.thread.title}`,
    tone: "success",
  };
```

The reducer verifies the payload Thread ID equals the selected Thread ID before replacing `state.thread.view.thread`. Do not make the Presenter mutate state and do not fetch a second title source.

- [ ] **Step 6: Verify pending queue behavior and command history**

```ts
it("queues rename behind an active turn and applies the latest submitted title", async () => {
  await submitWhileActive("/rename Cube helper");
  expect(pendingInputs()).toEqual(["/rename Cube helper"]);
  await finishActiveTurn();
  expect(currentThreadTitle()).toBe("Cube helper");
  expect(frame()).toContain("❯ /rename Cube helper");
  expect(frame()).toContain("Conversation renamed · Cube helper");
});
```

- [ ] **Step 7: Run command, protocol, TUI, and structural tests**

```bash
uv run pytest tests/unit/application/test_conversation_commands.py tests/unit/conversation/test_service.py tests/unit/protocol/test_contract_fixtures.py -q
npm --prefix tui run typecheck
npm --prefix tui test -- tests/commands/catalog-presenters.test.tsx tests/components/app-command-flow.test.tsx tests/pending-input/app-flow.test.tsx
rg -n "/new <title>|new.*title" src tui/src protocol/fixtures tests
```

Expected: tests pass; the final search finds no product path accepting a `/new` title argument.

- [ ] **Step 8: Commit Task 7**

```bash
git add src/awesome_agent/application src/awesome_agent/conversation/service.py tui/src/protocol/commands.ts tui/src/commands tui/src/app/App.tsx tui/src/state protocol/fixtures/v2/commands.json tests/unit/application tests/unit/conversation tui/tests/commands tui/tests/components tui/tests/pending-input
git commit -m "feat: add deterministic thread rename"
```

### Task 8: Anchor IME to the Composer and finalize documentation/regression evidence

**Files:**
- Create: `tui/src/components/use-composer-cursor.ts`
- Modify: `tui/src/components/Composer.tsx`
- Modify: `tui/src/app/App.tsx`
- Create: `tui/tests/components/composer-cursor.test.tsx`
- Test: `tui/tests/components/composer.test.tsx`
- Modify: `docs/user-guide/commands.md`
- Modify: `docs/architecture/protocol-and-ink.md`
- Modify: `docs/architecture/application-and-langgraph.md`
- Modify: `docs/architecture/storage.md`
- Modify: `docs/development/command-regression.md`
- Modify: `README.md` and `README.zh-CN.md` only if either currently states the changed defaults or command inventory.

**Interfaces:**
- Produces: `useComposerCursor({active, metrics, cursorRow, cursorColumn, hiddenAbove})` as a presentation-only hook.
- Consumes: existing `displayWidth()` and Ink `useCursor()` / `useBoxMetrics()`.
- Removes: fake `▌` cursor from Composer output.

- [ ] **Step 1: Write cursor ownership and display-width tests**

```tsx
it("positions the real cursor after wide input", () => {
  const cursor = renderComposerCursor({
    draft: "你好👩‍💻",
    cursorGrapheme: 3,
    measured: { left: 4, top: 10, width: 40, height: 4 },
  });
  expect(cursor.lastPosition()).toEqual({ x: 4 + 2 + displayWidth("❯ 你好👩‍💻"), y: 12 });
  expect(cursor.frame()).not.toContain("▌");
});

it("hides the Composer cursor when an exclusive interaction owns input", () => {
  const cursor = renderComposerCursor({ active: false });
  expect(cursor.lastPosition()).toBeUndefined();
});
```

Add wrapping, `hiddenAbove`, terminal resize, CJK combining marks, emoji, submitting, and unmount cases. Mock the Ink cursor context rather than snapshotting ANSI output.

- [ ] **Step 2: Run Composer tests and verify the fake cursor/current coordinates fail**

```bash
npm --prefix tui test -- tests/components/composer-cursor.test.tsx tests/components/composer.test.tsx
```

Expected: tests fail because no cursor hook exists and Composer still renders `▌`.

- [ ] **Step 3: Implement one real-cursor hook**

```ts
export function useComposerCursor({
  active,
  metrics,
  cursorRow,
  cursorColumn,
  hiddenAbove,
}: ComposerCursorOptions): void {
  const { setCursorPosition } = useCursor();
  if (!active || !metrics.hasMeasured) {
    setCursorPosition(undefined);
    return;
  }
  setCursorPosition({
    x: metrics.left + 2 + displayWidth("❯ ") + cursorColumn,
    y: metrics.top + 2 + Number(hiddenAbove) + cursorRow,
  });
}
```

Use a `Box` ref on the Composer's outer measured container and pass `active`
from `App.tsx` only when Composer mode owns input. The offsets are fixed by the
current component structure: one border column plus one horizontal padding
column, and one border row plus the title row. `hiddenAbove` contributes one
additional row. Do not duplicate those offsets outside this hook.

- [ ] **Step 4: Remove the fake cursor and retain the same visible draft**

```tsx
<Text>
  <Text bold color={theme.primary}>❯ </Text>
  {beforeCursor}
  {afterCursor}
</Text>
```

IME preedit remains terminal-owned. Do not copy preedit text into React state or install another `useInput` handler.

- [ ] **Step 5: Update implemented-behavior documentation**

Document:

- `/rename <title>`, its mandatory argument, 100-character limit, and queue behavior;
- `/new` with no title argument;
- first-message automatic titles and the 48-character bound;
- new Threads default Thinking On and resumed Threads retain their setting;
- structured Change facts and no empty Change output;
- real Ink cursor ownership and IME behavior;
- schema version 2's explicit development-data reset behavior, without presenting a compatibility migration.

Keep English and Chinese README content behaviorally consistent if either file needs changes. Remove the current sentence in `docs/user-guide/commands.md` that says Thinking defaults to Off.

- [ ] **Step 6: Run the complete scoped validation ladder**

```bash
uv run ruff format --check src tests
uv run ruff check src tests
uv run mypy
uv run pytest tests/unit/core/changes tests/unit/application/test_change_scope.py tests/unit/application/test_conversation_commands.py tests/unit/conversation tests/unit/storage/test_conversation_storage.py tests/unit/config tests/unit/protocol/test_contract_fixtures.py tests/structural/test_product_architecture.py -q
npm --prefix tui run format:check
npm --prefix tui run lint
npm --prefix tui run typecheck
npm --prefix tui test -- tests/protocol/contracts.test.ts tests/state/reducer.test.ts tests/transcript tests/components/change-summary.test.tsx tests/components/composer-cursor.test.tsx tests/components/composer.test.tsx tests/components/app-command-flow.test.tsx tests/pending-input/app-flow.test.tsx tests/surface/reconciliation.test.ts
npm --prefix tui run build
```

Expected: every command exits 0. Do not run live-provider, installer, full cross-host, or unrelated legacy suites as automatic gates.

- [ ] **Step 7: Perform the one required manual Windows IME acceptance check**

Run from the repository:

```powershell
uv run awesome-dev
```

Verify in Windows PowerShell:

1. Pinyin preedit appears at the visible Composer cursor, not at the terminal bottom.
2. Committing Chinese text inserts it exactly once.
3. CJK and emoji before the cursor do not shift the preedit anchor incorrectly.
4. Opening `/` menu, Picker, Approval, or Auth hides/reassigns the Composer cursor.
5. Returning to Composer restores the correct cursor position.

Record the terminal host and result in the PR. This manual check validates host IME integration; automated tests remain the source for coordinate logic.

- [ ] **Step 8: Verify the accepted end-to-end product behavior**

Using a fake Provider fixture or local deterministic test Core, verify:

```text
new Thread -> Thinking On
first message -> automatic title
tool read -> no Changed block
file edit -> ◇ 1 file changed -> Ctrl+O rows with +N/-N
/rename Cube helper -> title updates immediately
/new unexpected -> explicit invalid-arguments result
/resume -> manual title and Thinking setting restored
```

Also verify a model-tool-model Turn still shows two distinct real Thinking intervals.

- [ ] **Step 9: Inspect the final diff for replaced old logic**

```bash
git diff --check
git status --short
rg -n "Changed .*reversibility|workspace.changed|latest_change|changed_paths|/new <title>|defaults to off|>▌|▌" src tui/src protocol docs tests
```

Expected: no production match for the removed paths. Domain-only `ChangeReversibility` remains in Change Journal and Undo/Redo code.

- [ ] **Step 10: Commit Task 8**

```bash
git add tui/src/components/Composer.tsx tui/src/components/use-composer-cursor.ts tui/src/app/App.tsx tui/tests/components docs README.md README.zh-CN.md
git commit -m "fix: anchor ime and document thread behavior"
```

## Final Acceptance Checklist

- [ ] Read-only Tools never allocate or display an empty ChangeSet.
- [ ] Execute-only audit does not claim a file changed.
- [ ] `/diff` and `+N/-N` derive from one analyzer.
- [ ] Text, binary, directory, and symlink changes are exhaustively typed.
- [ ] Folded and expanded Change UI matches the accepted design.
- [ ] Internal reversibility enums never appear in terminal copy.
- [ ] New Threads default to Thinking On; per-Thread Off survives resume.
- [ ] Multiple real Thinking intervals remain unchanged.
- [ ] First accepted natural-language input names the Thread atomically.
- [ ] Automatic titles normalize whitespace and use `47 graphemes + …` when truncated.
- [ ] `/rename` requires a title, rejects more than 100 graphemes, persists manual provenance, and queues normally.
- [ ] `/new` accepts no hidden title argument.
- [ ] Reconciliation refreshes authoritative Thread metadata, not only transcript blocks.
- [ ] Windows Pinyin preedit follows the real Composer cursor.
- [ ] `TerminalInput` remains the only keyboard subscriber.
- [ ] No compatibility path, legacy payload, duplicate formatter, or temporary debug artifact remains.
