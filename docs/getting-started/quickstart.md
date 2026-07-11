# Quickstart

Follow these steps to start using Awesome in a few minutes.

## 1. Install Awesome

### macOS or WSL2 Ubuntu

```bash
curl -fsSL https://github.com/JAGGER-L/awesome_agent/releases/latest/download/install.sh | sh
```

### Windows

```powershell
irm https://github.com/JAGGER-L/awesome_agent/releases/latest/download/install.ps1 | iex
```

Open a new terminal, then check the installation:

```text
awesome --version
awesome --help
```

Git is optional. Awesome does not install it; use the
[official Git installer](https://git-scm.com/downloads) when your workflow needs
Git.

## 2. Configure a Model

`AWESOME_HOME` defaults to `%LOCALAPPDATA%\Awesome` on Windows and `~/.awesome`
on macOS or WSL2. Add one or both keys to `<AWESOME_HOME>/.env`:

```dotenv
DEEPSEEK_API_KEY=...
MOONSHOT_API_KEY=...
```

Awesome supports DeepSeek and Kimi. If both keys are present, select a model in
`<AWESOME_HOME>/config.yaml` or later with `/model`.

## 3. Start in a Project

```text
cd <project>
awesome
```

The directory where you start Awesome becomes the workspace.

## 4. Trust the Workspace

Awesome shows the workspace path before loading project instructions,
configuration, Skills, MCP servers, or tools. Choose Yes only when you recognize
and trust the project. Choose No to exit without saving trust.

## 5. Explore the Project

Start with one read-only request:

```text
Analyze this project's structure and tell me where I should start reading.
```

## 6. Make and Inspect a Change

Describe one small change you want. When Awesome finishes, inspect it with:

```text
/diff
```

## 7. Undo or Redo

For changes made through the file tools, use:

```text
/undo
/redo
```

Commands run through `execute` may affect files or external tools outside the
Change Journal, so their effects are not always reversible.

## 8. Find the Thread ID

```text
/status
```

`/status` shows the current Thread ID together with the workspace, model,
thinking mode, memory, MCP, and active operation state.

## 9. Continue Later

Exit with `/quit`, then reopen the latest or a selected Thread:

```text
awesome --continue
awesome --resume
awesome --resume <thread_id>
```

## 10. Next Steps

- [`/thinking`](../user-guide/commands.md) shows or changes thinking mode;
  thinking is default off.
- Local file Memory and Mem0 Cloud are independent and both default off; use
  `/memory` to inspect them.
- `/context` and `/usage` show context and model usage details.
- `/doctor` checks configuration, local state, and model readiness.

Continue with [Commands](../user-guide/commands.md),
[Configuration](../user-guide/configuration.md), or
[Troubleshooting](../user-guide/troubleshooting.md). To upgrade, close Awesome
and run the same installation command again.
