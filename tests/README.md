# Target Test Suite

This suite protects the implemented local-first product. It is not a
compatibility suite for deleted platform architecture.

## Test Layers

- `unit/`: Agent, Application, configuration, context, conversations, Core
  tools and changes, extensions, memory, model/provider contracts, protocol,
  safety, and embedded storage.
- `integration/`: cross-package local behavior including trust, SQLite,
  checkpoints, tools, changes, memory, extensions, Agent turns, recovery, and
  the headless product flow.
- `e2e/`: the real `awesome-core` stdio process with deterministic fake
  DeepSeek/Kimi boundaries.
- `structural/`: package, dependency, protocol, documentation, and architecture
  ownership constraints.
- `tui/tests/`: Ink behavior, reducers, lifecycle, real stdio flows, packaging,
  and TypeScript boundaries.

## Canonical Local Gate

```powershell
uv run --extra memory ruff format --check src tests scripts/generate_protocol_fixtures.py
uv run --extra memory ruff check src tests scripts/generate_protocol_fixtures.py
uv run --extra memory mypy src/awesome_agent
uv run --extra memory pytest -q tests/unit
uv run --extra memory pytest -q tests/integration
uv run --extra memory pytest -q tests/e2e
uv run --extra memory pytest -q tests/structural
uv run python scripts/generate_protocol_fixtures.py --check
uv build --wheel

npm --prefix tui ci
npm --prefix tui run format:check
npm --prefix tui run lint
npm --prefix tui run typecheck
npm --prefix tui test
npm --prefix tui run build
npm --prefix tui pack --dry-run
```

Use smaller affected subsets while developing, then run the complete target
gate before integration. Do not revive deleted interfaces to satisfy obsolete
tests.

## External Checks

Tests use deterministic fake Provider, Mem0, and MCP boundaries. Live DeepSeek,
Kimi, Mem0 Cloud, network, and cross-host installation checks are explicit
release validation only; they are never silently run by the local merge gate.
