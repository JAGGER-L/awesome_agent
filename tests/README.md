# Target Test Baseline

This directory contains tests that protect the accepted local-first target
architecture. It is intentionally not a compatibility suite for the platform
architecture being removed.

## Current Baseline

- `unit/`: provider-neutral model contracts, DeepSeek and Kimi adapters,
  built-in memory policy and storage, workspace identity and trust contracts,
  tool registry/executor/path policy, Change Journal lifecycle and operations,
  all eight built-in tools, slash-command contracts, local path safety,
  redaction, command policy, and host process lifecycle behavior;
- `structural/`: framework-free domain direction, repository harness
  separation, Markdown links, source package layout, and target storage
  and tool boundaries;
- `integration/`: local SQLite/LangGraph checkpoint persistence, workspace
  trust, bounded read-tool execution, and restart-safe controlled change,
  recursive delete, diff, undo, redo, plus the complete headless Phase 1
  trust/tool/interaction/cancellation/reopen vertical slice;
- `e2e/`: the protocol-only `awesome-core` subprocess workflow and the
  public Ink `awesome` workflow, including
  trust, commands, fake Provider tools, direct execution, cancellation,
  shutdown, and SQLite restart recovery through fake DeepSeek and Kimi;
- `../tui/tests/`: protocol, reducer, Ink component, composer, lifecycle,
  real-stdio product-flow, structural, and isolated npm-package verification.

## Deletion Rule

Delete a legacy test when all of the following are true:

1. it describes a component or user flow that the target architecture removes;
2. it asserts old implementation structure rather than an accepted target
   outcome;
3. keeping it would require a compatibility layer, external infrastructure, or
   a misleading skip.

Before deletion, preserve any still-valid behavior in the ledger below. Add the
replacement test when the owning target module is introduced.

## Deferred Coverage Ledger

| Target capability | Coverage to add with implementation |
| --- | --- |
| Agent loop | iteration budget, tool-call cycle, malformed message repair, model fallback, cancellation, context compression |
| Tool extensions | MCP discovery/execution and user tool isolation |
| Skills | discovery precedence, lazy loading, explicit slash selection, prompt-size limits, untrusted content handling |
| Configuration | defaults, user/workspace/environment precedence, validation, secret handling, restart semantics |
| Memory | `MEMORY.md`, `USER.md`, Mem0 Cloud adapter, fail-open behavior, opt-out, untrusted recall injection |
| Ink TUI | Manual terminal compatibility across additional terminal engines and operating systems |
| Sandbox | local process policy first; optional Docker execution backend only when implemented |
| Product readiness | fresh install, first run, real repository edit/test flow, smoke, recovery, and performance regression tests |

## Running Tests

During the architecture rewrite, run focused target gates rather than obsolete
Textual/API/PostgreSQL/Worker/Docker suites. The Phase 2 product checks are:

```powershell
uv run --extra memory pytest -q tests/unit/application tests/unit/agent tests/unit/context tests/unit/conversation tests/unit/core tests/unit/modeling tests/unit/providers tests/unit/extensions tests/unit/memory tests/unit/protocol tests/unit/storage
uv run --extra memory pytest -q tests/integration/test_headless_product.py tests/e2e/test_stdio_product.py
uv run --extra memory pytest -q tests/structural/test_target_*.py tests/structural/test_markdown_links.py
```

Phase 3 adds these Node and packaging gates:

```powershell
npm --prefix tui ci
npm --prefix tui run format:check
npm --prefix tui run lint
npm --prefix tui run typecheck
npm --prefix tui test
npm --prefix tui run build
npm --prefix tui pack --dry-run
npm --prefix tui test -- tests/e2e tests/packaging tests/structural
```

The fake Provider/Mem0/MCP boundaries are deterministic and networkless. Live
DeepSeek, Kimi, and Mem0 Cloud behavior remains an external, unverified release
risk rather than a merge dependency.

For a task in progress, run affected files first. Obsolete Textual, API,
PostgreSQL, Worker, Docker, and hosted suites are not Phase 3 merge gates; they
must not force compatibility layers into the replacement architecture. The
complete target baseline is required before merging back into
`codex/local-first-architecture`.
