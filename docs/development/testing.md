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

## Automated GitHub Gates

Pull requests, pushes to `main`, and merge-queue revisions run the tracked
`Required CI` workflow. Its stable `Required` job aggregates Python quality,
coverage, Windows-sensitive contracts, structural and packaging contracts, and
the TUI matrix. Configure both `Required` and `Security required` as
branch-ruleset status checks; do not require individual matrix-generated names.

The security workflow runs CodeQL for Python and TypeScript, reviews new
dependencies on pull requests, validates the exported Python lock through
pip-audit's PyPI-backed pip hash-checking path, supplements that vulnerability
result with OSV, and audits the locked npm dependency graph. The OSV pass consumes
the already validated exact name/version graph; it does not independently
validate artifact hashes. The nightly workflow runs the complete Python and TUI
suites on Ubuntu, Windows, and macOS. Third-party Actions are pinned to full
commit hashes, every job has a deadline, and pull-request runs cancel stale
revisions. Required CI also downloads a checksum-pinned `actionlint` binary and
validates every tracked workflow before running the language gates.
Dependabot monitors the uv lock, TUI npm lock, and immutable GitHub Action
references on separate weekly schedules.

Platform-specific filesystem and process tests must use explicit platform
markers or dialect parameters. A platform skip is evidence that remains to be
collected on the corresponding runner; it is not a passing substitute.

## Release Gate

Before a release candidate, run:

```powershell
uv sync --locked --extra memory --dev
uv run ruff format --check src tests scripts
uv run ruff check src tests scripts
uv run mypy src tests scripts
uv run --extra memory pytest -q tests/unit
uv run --extra memory pytest -q tests/integration
uv run --extra memory pytest -q tests/e2e
uv run --extra memory pytest -q tests/packaging tests/structural
uv run python scripts/generate_protocol_fixtures.py --check
uv lock --check
$AuditRequirements = Join-Path ([System.IO.Path]::GetTempPath()) `
  "awesome-agent-release-requirements-$PID.txt"
try {
  uv export --locked --extra memory --no-dev --no-emit-project `
    --format requirements.txt --output-file $AuditRequirements
  uv run pip-audit --require-hashes --progress-spinner off `
    --vulnerability-service pypi --requirement $AuditRequirements
  uv run pip-audit --require-hashes --disable-pip --progress-spinner off `
    --vulnerability-service osv --requirement $AuditRequirements
} finally {
  Remove-Item -LiteralPath $AuditRequirements -ErrorAction SilentlyContinue
}
uv build --wheel --no-build-isolation

npm --prefix tui ci
node tui/scripts/sync-version.mjs --check
npm --prefix tui run format:check
npm --prefix tui run lint
npm --prefix tui run typecheck
npm --prefix tui test
npm --prefix tui run build
npm --prefix tui audit --package-lock-only --audit-level=high
npm pack ./tui --dry-run
```

The first pip-audit command must not use `--disable-pip`: its isolated pip
resolution is what checks the exported hashes. The second command is the
supplemental OSV lookup. Release CI then builds the bundle once on Ubuntu and
runs the same verifier against that downloaded artifact on Windows and macOS
before the tag-only attestation job can start.

Live DeepSeek, Kimi, Mem0 Cloud, network, and installation checks are explicit
release evidence. Normal deterministic tests do not require credentials.

Run the live service release checks only in a temporary process environment:

```powershell
$env:AWESOME_RUN_EXTERNAL = "1"
uv run --extra memory pytest -q tests/external/test_release_services.py
Remove-Item Env:AWESOME_RUN_EXTERNAL, Env:DEEPSEEK_API_KEY, `
  Env:MOONSHOT_API_KEY, Env:MEM0_API_KEY -ErrorAction SilentlyContinue
```

Set `DEEPSEEK_API_KEY`, `MOONSHOT_API_KEY`, and `MEM0_API_KEY` outside the
repository before running the command. Record only test status, duration, and
redacted diagnostic codes. Never write credential values into project files or
release evidence.

Record exact commands and outcomes. When an environmental gate is unavailable,
state the reason and remaining risk rather than reporting it as passing.
