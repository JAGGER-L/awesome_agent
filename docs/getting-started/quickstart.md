# Quickstart

Follow these five steps to install Awesome and complete your first successful
session.

## 1. Install Awesome

### macOS or WSL2 Ubuntu

```bash
curl -fsSL https://github.com/JAGGER-L/awesome_agent/releases/latest/download/install.sh | sh
```

### Windows

```powershell
irm https://github.com/JAGGER-L/awesome_agent/releases/latest/download/install.ps1 | iex
```

Open a new terminal and verify the installation:

```text
awesome --version
```

Git is optional. Awesome does not install it; use the
[official Git installer](https://git-scm.com/downloads) if your work needs Git.

## 2. Start in a Project

```text
cd <project>
awesome
```

The directory where you launch Awesome becomes the workspace.

## 3. Trust the Workspace

Awesome shows the workspace path before using project instructions or tools.
Choose Yes only when you recognize and trust the project. Choose No to exit.
After trust, Awesome starts in Request approval mode and asks before edits,
deletes, and shell commands. Run `/permissions` if you want to review the mode.

## 4. Configure a Model

When no model Provider is configured, Awesome shows a setup notice. Press Enter
or run `/model`. Choose DeepSeek or Kimi, paste the API key into the masked
input, then choose a model. The key is validated before it is saved.

Use `/auth` later to add, replace, or remove Provider credentials. Never put an
API key in a slash-command argument or chat message.

## 5. Verify Your Setup

Send one read-only request:

```text
Analyze this project's structure and tell me where I should start reading.
```

## Learn More

- [Commands](../user-guide/commands.md)
- [Configuration](../user-guide/configuration.md)
- [Troubleshooting](../user-guide/troubleshooting.md)

## Develop from Source

Use the source workflow when you want to change Awesome itself. It runs the
same Python Core, private stdio protocol, and Ink TUI as an installed release,
while keeping development data separate.

### Prerequisites

Install [Git](https://git-scm.com/downloads),
[uv](https://docs.astral.sh/uv/getting-started/installation/), and
[Node.js 22 or newer](https://nodejs.org/). npm is included with Node.js. You
do not need to install Python separately; uv installs the required Python 3.12
runtime for the project.

### Clone the Repository

```text
git clone https://github.com/JAGGER-L/awesome_agent.git
cd awesome_agent
```

### Install Development Dependencies

```text
uv sync --locked --extra memory
npm ci --prefix tui
```

The first command creates `.venv`, installs the Python Core in editable mode,
and installs the optional Mem0 Cloud integration. The second installs the
locked TUI dependencies under `tui/node_modules`.

### Start Awesome

From the repository root, run:

```text
uv run awesome-dev
```

`awesome-dev` checks the local prerequisites, builds the current TUI source,
adds the editable Python Core to the child process path, and starts Awesome in
the current directory. The Core still runs as the TUI's private child process;
you do not need to start a second terminal or server.

To work on another project while running the Awesome source checkout:

```text
uv run awesome-dev --workspace <project-path>
```

The selected directory becomes the workspace and goes through the normal trust
prompt. Development state is stored in the ignored `.awesome-dev/home`
directory inside the Awesome repository, and development logs are reserved
under `.awesome-dev/logs`. Nothing is written to the target workspace except
changes you approve or request from the Agent.

### Configure a Model

Model setup is the same in development and installed builds. Start Awesome,
run `/auth`, choose DeepSeek or Kimi, select an available credential source,
and use the masked API-key input when needed. Credentials are stored under the
development home, not in the repository or target workspace. Set
`AWESOME_HOME` before launching only when you intentionally want a different
development data directory.

### Run Checks

Start with checks that cover your change:

```text
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run pytest <relevant-test-paths> -q
npm --prefix tui run format:check
npm --prefix tui run lint
npm --prefix tui run typecheck
npm --prefix tui test -- --run <relevant-test-paths>
```

Before a release or a cross-component handoff, run the full Python and TUI
suites described in the [testing guide](../development/testing.md).

### Continue After Code Changes

Stop the current session and run `uv run awesome-dev` again. The launcher
rebuilds the TUI on every start, and editable Python imports use the latest
source. Development mode intentionally has no hot reload because restarting a
Core during an active Thread could leave operations in an ambiguous state.

To build the production TUI without starting Awesome, run:

```text
npm --prefix tui run build
```

Release bundles and installers use a separate packaging flow; see the
[release guide](../development/release.md).

### Troubleshooting

- `uv sync --locked --extra memory` fixes a missing `.venv` or
  `awesome-core` entry point.
- `npm ci --prefix tui` fixes a missing `tui/node_modules` directory.
- Install Node.js 22 or newer when the launcher reports an unsupported Node
  version or cannot find npm.
- Pass an existing directory to `--workspace`; the launcher rejects missing
  paths instead of creating them.
- Run the command in an interactive terminal. The Ink interface cannot run
  through a non-interactive pipe.

If a source checkout reports that its state schema is incompatible, stop
Awesome before changing any files. Confirm that the state path shown in the
startup panel is the repository-local development path:

```powershell
Resolve-Path .\.awesome-dev\home\state
Remove-Item -LiteralPath .\.awesome-dev\home\state -Recurse -Force
uv run awesome-dev
```

On macOS or WSL2:

```bash
realpath .awesome-dev/home/state
rm -rf -- .awesome-dev/home/state
uv run awesome-dev
```

Remove only the verified `state` directory. Development configuration and
credentials are stored outside that directory and remain unchanged. This
resets disposable development conversations and checkpoints; it is not a data
migration.

Development mode does not replace or modify an installed `awesome` command.
Installed Awesome uses its normal user data directory and prebuilt release
files; `uv run awesome-dev` uses the current checkout, rebuilds the TUI, and
defaults to the repository-local ignored `.awesome-dev` directory.
