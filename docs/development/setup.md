# Development setup

This guide creates a reproducible local checkout for Python Core, Ink TUI,
tests, and documentation work.

## Requirements

- Git;
- [uv](https://docs.astral.sh/uv/) for the locked Python 3.12 environment;
- Node.js 22.23.1 or newer with npm. The TUI package declares 22.23.1 as its
  minimum, and CI also exercises Node 24;
- a supported host terminal on Windows, macOS, or Linux.

`uv` provisions the repository's exact Python 3.12 environment. Do not install
project dependencies into a global Python environment.

For development, Git and Node are host prerequisites. The end-user installer
has a separate private-runtime flow and is not the contributor setup method.

## Clone and create a branch

```powershell
git clone https://github.com/JAGGER-L/awesome_agent.git
Set-Location awesome_agent
git switch -c codex/my-change
git status --short --branch
```

Use a dedicated branch or worktree. Before editing, read `AGENTS.md`, inspect
an active `.codex/exec-plans/active/` plan when present, and confirm whether
another task owns overlapping files.

## Install locked dependencies

From the repository root:

```powershell
uv sync --locked --extra memory --dev
npm ci --prefix tui
```

Why these commands:

- `--locked` refuses to resolve a dependency graph different from `uv.lock`;
- `--extra memory` installs the optional Mem0 adapter so the complete local
  test suite can import and exercise it;
- `--dev` installs Ruff, mypy, pytest, coverage, and audit tooling;
- `npm ci` installs exactly `tui/package-lock.json` and rejects lock drift.

For a narrow Python-only change that cannot touch Memory, `uv sync --locked
--dev` is sufficient. Use the full environment before the complete suite or a
release candidate.

## Start the current checkout

```powershell
uv run awesome-dev
```

The development launcher:

1. verifies the checkout, Node/npm, `.venv`, and TUI dependencies;
2. runs the TUI production build;
3. sets `AWESOME_HOME` to the ignored `.awesome-dev/home` unless already set;
4. adds the checkout's `awesome-core` entry point to the child `PATH`;
5. starts the built TUI in the current directory.

Open a different workspace with:

```powershell
uv run awesome-dev --workspace C:\path\to\project
```

Development state and the reserved log directory default to:

```text
.awesome-dev/
  home/    # isolated Awesome state/config/memory for this checkout
  logs/    # reserved by the launcher; not currently populated automatically
```

This prevents ordinary development from using the installed product's home.
Core stderr is currently held in the TUI's bounded in-memory ring for lifecycle
diagnostics, not persisted under `logs/`. The selected project still remains a
real host workspace, so treat trust and tool approvals normally.

## Configure a test provider safely

Interactive provider setup can be done through `/auth`. For local automated
tests, deterministic suites use fakes and need no credential.

Live release checks read process-scoped environment variables. Prompt for real
values so they are not embedded in the command line or shell history:

```powershell
$env:AWESOME_RUN_EXTERNAL = "1"
$secret = Read-Host "DeepSeek API key" -AsSecureString
$env:DEEPSEEK_API_KEY = [Net.NetworkCredential]::new("", $secret).Password
$secret = Read-Host "Moonshot API key" -AsSecureString
$env:MOONSHOT_API_KEY = [Net.NetworkCredential]::new("", $secret).Password
$secret = Read-Host "Mem0 API key" -AsSecureString
$env:MEM0_API_KEY = [Net.NetworkCredential]::new("", $secret).Password
Remove-Variable secret
uv run --extra memory pytest -q tests/external/test_release_services.py
Remove-Item Env:AWESOME_RUN_EXTERNAL, Env:DEEPSEEK_API_KEY, `
  Env:MOONSHOT_API_KEY, Env:MEM0_API_KEY -ErrorAction SilentlyContinue
```

For ordinary product use, prefer `/auth`; for CI, use the platform's secret
injection rather than literal workflow values. Never place real values in shell
history, tracked `.env` files, fixtures, snapshots, logs, plans, or PR
descriptions. Record only redacted outcomes and diagnostic codes.

## Fast environment check

```powershell
uv lock --check
uv run python --version
node --version
npm --version
uv run python -c "import awesome_agent; print(awesome_agent.__version__)"
node tui/scripts/sync-version.mjs --check
```

The Python version must satisfy `>=3.12,<3.13`. The Core package version, TUI
package/lock/generated source, installers, and `VERSION` are checked elsewhere;
do not edit generated version files by hand.

## Common setup failures

### `awesome-dev` cannot find Core

Run:

```powershell
uv sync --locked --extra memory --dev
```

The launcher expects `awesome-core` under `.venv/Scripts` on Windows or
`.venv/bin` on POSIX.

### TUI dependencies are missing

Run:

```powershell
npm ci --prefix tui
```

Do not substitute `npm install`; it can change the lockfile.

### Node is rejected

Use Node 22.23.1 or a newer compatible release. `awesome-dev` checks major 22
or newer, while the package engine and CI provide the stricter supported
baseline.

### TUI build fails on version check

First inspect `VERSION`, `tui/package.json`, `tui/package-lock.json`, and
`tui/src/version.ts`. For an intentional version change run:

```powershell
npm --prefix tui run version:sync
```

Review every resulting file. For ordinary feature work, revert unintended
version edits rather than syncing them.

### Application state is incompatible

Do not manually delete databases. Start the product and follow its typed
reset-or-exit flow for an older schema. A newer or unknown schema is a stop
condition; use the correct checkout or upgrade path.

### Another session owns the workspace

Close the other Awesome session that selected the same workspace. Path and
physical-identity leases intentionally prevent concurrent Core authorities.

### Clean checkout, stale generated output

TUI build removes and regenerates `tui/dist`. Release output lives under
`dist/`. These are generated artifacts; do not commit them unless a repository
contract explicitly changes.

## IDE and shell notes

- Run Python tools through `uv run` so editor/terminal results use the locked
  environment.
- Point a Python language server to `.venv` and enable strict type checking.
- Keep line endings as LF; Ruff and Biome enforce repository formatting.
- On Windows, use PowerShell-native file operations for repository work and
  avoid moving/deleting recursive targets through mixed shells.
- Platform-specific filesystem and process behavior must be tested on that
  platform; a Linux simulation is not evidence for a Windows junction or Job
  Object contract.

Next, choose the smallest gate in [Testing](testing.md).
