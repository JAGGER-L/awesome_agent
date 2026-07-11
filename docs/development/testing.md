# Testing

Tests protect observable Local-first product behavior and stable contracts, not
removed implementation structure.

## While changing a module

Run the smallest sufficient sequence: format/lint, affected type checks,
targeted unit tests, affected structural contracts, then only the integration
or E2E path crossed by the change. Do not restore a deleted interface merely to
make an obsolete test pass. When behavior remains but a test is coupled to old
structure, rewrite the test around the retained boundary.

Documentation-only work normally runs Markdown link/inventory tests and scans
examples for removed commands. Packaging or startup changes also require a
local install/startup smoke.

## Complete retained gate

Run before final integration or a release candidate:

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

Tests use deterministic fake DeepSeek, Kimi, Mem0, and MCP boundaries. Live
credentials, cross-host installation, and external network checks are explicit
release checks, not normal PR tests.

Record the exact commands and outcomes. If an environmental gate is
unavailable, state it and retain the risk; never report it as passing.
