# Target Test Baseline

This directory contains tests that protect the accepted local-first target
architecture. It is intentionally not a compatibility suite for the platform
architecture being removed.

## Current Baseline

- `unit/`: provider-neutral model contracts, OpenAI and DeepSeek adapters,
  built-in memory policy and storage, workspace identity and trust contracts,
  tool registry/executor/path policy, Change Journal lifecycle and operations,
  all eight built-in tools, slash-command contracts, local path safety,
  redaction, command policy, and host process lifecycle behavior;
- `structural/`: framework-free domain direction, repository harness
  separation, Markdown links, source package layout, and target storage
  and tool boundaries;
- `integration/`: local SQLite/LangGraph checkpoint persistence, workspace
  trust, bounded read-tool execution, and restart-safe controlled change,
  recursive delete, diff, undo, and redo behavior;
- `e2e/`: intentionally empty until the target CLI and Ink/React TUI flows
  exist.

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
| Workspace safety | application interaction flow for detected execute boundary crossings |
| Tool extensions | MCP discovery/execution and user tool isolation |
| Skills | discovery precedence, lazy loading, explicit slash selection, prompt-size limits, untrusted content handling |
| Configuration | defaults, user/workspace/environment precedence, validation, secret handling, restart semantics |
| Memory | `MEMORY.md`, `USER.md`, Mem0 Cloud adapter, fail-open behavior, opt-out, untrusted recall injection |
| CLI and TUI | slash-command contracts, event rendering, interaction prompts, cancellation, resume, Ink/React end-to-end flows |
| Sandbox | local process policy first; optional Docker execution backend only when implemented |
| Product readiness | fresh install, first run, real repository edit/test flow, smoke, recovery, and performance regression tests |

## Running Tests

Run the current target baseline with:

```powershell
.\scripts\check.ps1
```

For a task in progress, run the affected test files first. The complete current
baseline is required before merging that task back into
`codex/local-first-architecture`.
