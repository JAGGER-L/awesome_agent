# Testing

Tests protect current user behavior, package boundaries, and public contracts.
Choose the smallest validation set that covers the risk of each change.

## Fast Development Gate

Run formatting and lint first, followed by affected type checks and unit tests.
Stop when a lower-level gate fails instead of hiding it behind heavier output.

## Structural Contracts

`tests/structural/` verifies package inventory, dependency direction, framework
ownership, model and tool boundaries, commands, version authority,
documentation links, packaging inputs, and repository shape.

## Affected Integration Tests

Add the relevant integration path when work crosses workspace trust, SQLite or
checkpoints, Agent Turns, tool execution, Memory, MCP, or the JSON-RPC stdio
boundary. Documentation-only work normally runs Markdown link/inventory and
product-copy checks.

## Release Gate

Before a release candidate, run:

```powershell
uv sync --extra memory --dev
uv run --extra memory ruff format --check src tests scripts
uv run --extra memory ruff check src tests scripts
uv run --extra memory mypy src/awesome_agent scripts/release
uv run --extra memory pytest -q tests/unit
uv run --extra memory pytest -q tests/integration
uv run --extra memory pytest -q tests/e2e
uv run --extra memory pytest -q tests/packaging tests/structural
uv run python scripts/generate_protocol_fixtures.py --check
uv lock --check
uv build --wheel

npm --prefix tui ci
node tui/scripts/sync-version.mjs --check
npm --prefix tui run format:check
npm --prefix tui run lint
npm --prefix tui run typecheck
npm --prefix tui test
npm --prefix tui run build
npm pack ./tui --dry-run
```

Live DeepSeek, Kimi, Mem0 Cloud, network, and installation checks are explicit
release evidence. Normal deterministic tests do not require credentials.

Record exact commands and outcomes. When an environmental gate is unavailable,
state the reason and remaining risk rather than reporting it as passing.
