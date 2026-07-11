# Quickstart

This guide takes a fresh supported host to one trusted local coding session.

## 1. Install and open a new terminal

Apple Silicon macOS or WSL2 Ubuntu 24.04 x64:

```bash
curl -fsSL https://github.com/JAGGER-L/awesome_agent/releases/latest/download/install.sh | sh
```

Windows 11 x64 PowerShell:

```powershell
irm https://github.com/JAGGER-L/awesome_agent/releases/latest/download/install.ps1 | iex
```

Open a new terminal so its PATH includes `awesome`, then verify:

```text
awesome --version
awesome --help
```

Git is optional. Awesome does not install it; use the
[official installer](https://git-scm.com/downloads) if required.

## 2. Configure a Provider

`AWESOME_HOME` defaults to `%LOCALAPPDATA%\Awesome` on Windows and `~/.awesome`
on macOS/WSL2. Create `<AWESOME_HOME>/.env` with one or both keys:

```dotenv
DEEPSEEK_API_KEY=...
MOONSHOT_API_KEY=...
```

If both are present, select a model in `<AWESOME_HOME>/config.yaml` or later
with `/model`. Only DeepSeek and Kimi are supported.

## 3. Trust a workspace

```text
cd <workspace>
awesome
```

Review the displayed path. Choose Yes only if you trust its files and project
instructions. Choose No to exit without persisting trust.

Start with a harmless read request such as: `List the top-level files and
explain the project without changing anything.`

## 4. Make and review one edit

Ask for a small edit. Then use:

```text
/diff
/undo
/redo
```

Undo/redo covers journaled file-tool changes. Shell side effects from
`execute` are not guaranteed reversible.

## 5. Continue later

Use `/status` to copy the resumable Thread ID, then exit with `/quit`.

```text
awesome --continue
awesome --resume
awesome --resume <thread_id>
```

`--continue` uses the latest thread in the current workspace. `/resume` inside
the TUI provides the same thread-oriented workflow.

## Defaults and diagnostics

- `/thinking` shows the current mode and supports on/off selection;
  `/thinking on` and `/thinking off` set it. Thinking is default off.
- Local file memory and Mem0 Cloud are independent and both default off. Use
  `/memory` to inspect or configure them.
- `/status` shows product, workspace, thread, model, mode, memory, MCP,
  operation, and configuration status.
- `/context` and `/usage` show context and latest usage details separately.
- `/doctor` checks local configuration, SQLite, checkpoints, and Provider
  readiness.

For failures, see [Troubleshooting](../user-guide/troubleshooting.md). To
upgrade, close every Awesome process and rerun the original install command.
There is no separate update command.
