# Awesome Agent

[English](README.md) | [简体中文](README.zh-CN.md)

```text
  ███  █   █ █████ █████  ███  █   █ █████
 █   █ █   █ █     █     █   █ ██ ██ █
 █████ █ █ █ ████  █████ █   █ █ █ █ ████
 █   █ ██ ██ █         █ █   █ █   █ █
 █   █ █   █ █████ █████  ███  █   █ █████
```

Awesome is a local-first AI coding agent that works inside the directory you
launch it from; it is not a hosted service or a general Agent Platform.

V1.0.0 is a limited pilot for a few users. Supported hosts are Apple Silicon
macOS, Windows 11 x64, and WSL2 Ubuntu 24.04 x64.

## Install

On Apple Silicon macOS or WSL2 Ubuntu 24.04 x64:

```bash
curl -fsSL https://github.com/JAGGER-L/awesome_agent/releases/latest/download/install.sh | sh
```

On Windows 11 x64 PowerShell:

```powershell
irm https://github.com/JAGGER-L/awesome_agent/releases/latest/download/install.ps1 | iex
```

Open a new terminal after installation. Python, Node.js, uv, npm, Docker, and
Make are not installation prerequisites. Git is optional and is never installed
by Awesome; install it from the [official Git site](https://git-scm.com/downloads)
if you want Git-aware workflows.

## First run

```text
cd <workspace>
awesome
```

The launch directory is the workspace. Awesome asks for trust before reading
project configuration, instructions, Skills, MCP declarations, or running
tools. Declining exits without trusting the directory.

Configure at least one model key in `<AWESOME_HOME>/.env`:

```dotenv
DEEPSEEK_API_KEY=...
# or
MOONSHOT_API_KEY=...
```

DeepSeek and Kimi are the only supported Providers in V1. See the
[quickstart](docs/getting-started/quickstart.md) for model selection and the
first safe task.

## What it can do

The initial default tools are `ls`, `read_file`, `write_file`, `edit_file`,
`delete`, `glob`, `grep`, and `execute`. Extensions can add MCP and user tools;
the architecture is not limited to eight tools. File changes are recorded in a
Change Journal for `/diff`, `/undo`, and `/redo`.

Local `USER.md`/workspace `MEMORY.md` memory and Mem0 Cloud memory are
independent and default off. Skills provide task instructions; MCP connects
external tools. Neither can bypass workspace trust or tool policy.

## Launch options

```text
awesome
awesome --continue
awesome --resume
awesome --resume <thread_id>
awesome --version
awesome --help
```

## Documentation

- [Quickstart](docs/getting-started/quickstart.md)
- [Architecture](ARCHITECTURE.md)
- [Development](docs/development/README.md)

## Security

Awesome runs tools on the local host today; there is no Docker sandbox. Trust
only workspaces you understand, review diffs, and keep credentials in the
operating-system environment or `<AWESOME_HOME>/.env`, never in project files.
