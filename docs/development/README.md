# Development

This guide is for contributors to the Python Core and Ink surface. Product use
starts from the root [README](../../README.md).

## Setup

Contributor prerequisites are uv, Node.js 22 with npm, and Git. uv provisions
the repository's Python 3.12 runtime, so a separate Python installation is not
required.

```powershell
uv sync --locked --extra memory
npm ci --prefix tui
uv run awesome-dev
```

Use `uv run awesome-dev --workspace <project-path>` to open another project.
The complete source startup flow and troubleshooting steps are in the
[Quickstart](../getting-started/quickstart.md#develop-from-source).

## Repository Loop

1. Confirm the repository root, branch or worktree, and `git status`.
2. Read `AGENTS.md` and the active ignored plan when one exists.
3. Inspect the affected contracts, callers, tests, and documentation.
4. Make one scoped change and preserve unrelated work.
5. Run the lightest relevant checks from [Testing](testing.md).
6. Record commands, results, deferred checks, and remaining risk.
7. Inspect diff and status before a focused commit.

`.codex/` is ignored temporary development coordination state. Keep only an
active plan and explicitly accepted pending work while they are useful, then
remove task artifacts after handoff. These files never define Awesome product
behavior.

## Generated Contracts and Packages

```powershell
uv run python scripts/generate_protocol_fixtures.py --check
node tui/scripts/sync-version.mjs --check
uv lock --check
uv build --wheel --no-build-isolation
npm pack ./tui --dry-run
```

`VERSION` is the only manually maintained version source. Use
`scripts/release/build_bundle.py` for a release candidate; see the
[release checklist](release.md).
