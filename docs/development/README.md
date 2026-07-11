# Development

This guide is for contributors to the Python Core and Ink surface. Product use
starts from the root [README](../../README.md).

## Setup

Contributor prerequisites are Python 3.12, uv, Node.js 22, npm, and Git.

```powershell
uv sync --extra memory --dev
npm --prefix tui ci
```

No service, container, task runner, or external database is required.

## Repository loop

1. Confirm repository root, branch/worktree, and `git status`.
2. Read `AGENTS.md` and the relevant ignored plan under
   `.codex/exec-plans/active/` when one exists.
3. Inspect the affected contracts, callers, tests, and current docs.
4. Make one scoped change and preserve unrelated work.
5. Run the lightest relevant checks from [Testing](testing.md).
6. Record commands, results, skipped checks, and remaining risk.
7. Inspect diff/status before a focused commit.

`.codex/` contains local development coordination state and is ignored. Keep
only the current plan in `active/`, accepted future plans in `pending/`, and
closed plans in `completed/`. These files never define Awesome runtime
behavior and are not committed by default.

## Generated contracts and packages

```powershell
uv run python scripts/generate_protocol_fixtures.py --check
node tui/scripts/sync-version.mjs --check
uv lock --check
uv build --wheel
npm --prefix tui pack --dry-run
```

`VERSION` is the only manually maintained version source. Use
`scripts/release/build_bundle.py` only for a release candidate; see the
[manual release checklist](release.md).
