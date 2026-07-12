# Awesome

[English](README.md) | [简体中文](README.zh-CN.md)

```text
  ███  █   █ █████ █████  ███  █   █ █████
 █   █ █   █ █     █     █   █ ██ ██ █
 █████ █ █ █ ████  █████ █   █ █ █ █ ████
 █   █ ██ ██ █         █ █   █ █   █ █
 █   █ █   █ █████ █████  ███  █   █ █████
```

Awesome is an AI coding assistant that runs in your terminal. It can understand
your codebase, edit files, run commands, and help with development, debugging,
refactoring, and testing.

Start `awesome` in a project directory and describe your goal in natural
language. Awesome reads the relevant code, uses the tools it needs, makes the
change, and helps verify the result.

## What Awesome Can Do

- understand a project and explain how its code fits together;
- implement, debug, refactor, and test code;
- show controlled file changes with `/diff`, `/undo`, and `/redo`;
- continue the latest Thread or resume one by ID;
- choose between per-operation approval and Thread-scoped Full access;
- extend tasks with Skills, MCP tools, local Memory, and Mem0 Cloud;
- work with DeepSeek and Kimi models.

Awesome starts with `ls`, `read_file`, `write_file`, `edit_file`, `delete`,
`glob`, `grep`, and `execute`. Extensions can add more tools; the total is not limited to eight.
Local file Memory and Mem0 Cloud are independent and default off.

## Install

### macOS or WSL2 Ubuntu

```bash
curl -fsSL https://github.com/JAGGER-L/awesome_agent/releases/latest/download/install.sh | sh
```

### Windows

```powershell
irm https://github.com/JAGGER-L/awesome_agent/releases/latest/download/install.ps1 | iex
```

Open a new terminal after installation. Awesome includes the runtimes it needs;
you do not need to install Python, Node.js, uv, or npm first. Git is optional and
is never installed by Awesome. Install it from the
[official Git site](https://git-scm.com/downloads) when you want Git-aware
workflows.

## Start Awesome

Start Awesome inside a project:

```text
cd <project>
awesome
```

The first time Awesome opens a directory, it shows the path and asks whether
you trust it. Choose Yes only for projects you are comfortable allowing Awesome
to read and work in. Awesome starts in Request approval mode; use
`/permissions` to review or change the active Thread's mode.

If no model Provider is configured, press Enter or run `/model`. Choose
DeepSeek or Kimi, paste the API key into the masked input, then select a model.
Use `/auth` later to add, replace, or remove credentials.

Useful launch options:

```text
awesome --continue
awesome --resume
awesome --resume <thread_id>
awesome --version
awesome --help
```

## First Task

Try a read-only introduction to the project:

```text
Analyze this project's structure and tell me where I should start reading.
```

## Documentation

- [Quickstart](docs/getting-started/quickstart.md)
- [Commands](docs/user-guide/commands.md)
- [Configuration](docs/user-guide/configuration.md)
- [Workspace and tools](docs/user-guide/workspace-and-tools.md)
- [Memory, Skills, and MCP](docs/user-guide/memory-skills-mcp.md)
- [Troubleshooting](docs/user-guide/troubleshooting.md)
- [Architecture](ARCHITECTURE.md)
- [Development](docs/development/README.md)
- [Roadmap](docs/roadmap.md)

Contributors can run the current checkout with `uv run awesome-dev`; see
[Develop from Source](docs/getting-started/quickstart.md#develop-from-source)
for the complete setup and troubleshooting flow.

## Security

Only trust projects you understand. Review `/diff` before keeping changes, and
enter credentials only through Awesome's masked `/model` or `/auth` flow. Full
access is Thread-scoped and does not disable hard safety denials.
Process-environment variables and `<AWESOME_HOME>/.env` remain advanced
configuration options; never put credentials in project files.
