# Quickstart

Use these five steps to complete one successful, read-only Awesome session.
For host requirements, installer behavior, upgrades, and repair, see
[Installation](installation.md).

## 1. Install Awesome

On Apple Silicon macOS or WSL2 Ubuntu 24.04 x64:

```bash
curl -fsSL https://github.com/JAGGER-L/awesome_agent/releases/latest/download/install.sh | sh
```

On Windows 11 x64:

```powershell
irm https://github.com/JAGGER-L/awesome_agent/releases/latest/download/install.ps1 | iex
```

Open a new terminal and verify the release:

```text
awesome --version
```

Awesome includes its Python and Node.js runtimes. Git is optional and available
from the [official Git installer](https://git-scm.com/downloads).

## 2. Start in a Project

Choose a project you recognize, then start Awesome from its root:

```text
cd <project>
awesome
```

The launch directory becomes the Workspace. Use `awesome --continue` later to
resume its most recent Thread, or `awesome --resume` to choose one.

## 3. Trust the Workspace

Verify the displayed path before choosing **Yes**. Choosing **No** exits without
loading project configuration, project instructions, or tools for that
Workspace.

After trust, Awesome starts in **Request approval** mode. Reads are allowed;
writes, deletes, and shell commands ask first. `/permissions` shows the current
mode. Trust is not an operating-system sandbox, so use external isolation for a
project you do not trust.

If a plain root `AGENTS.md` is accepted, Awesome snapshots it once for this
session as mandatory project instructions. A rejected instruction file is
ignored whole and reported in Welcome, the status line, and `/doctor`.

## 4. Configure a Model

When no model Provider is configured, press Enter on the setup notice or run:

```text
/model
```

Choose DeepSeek or Kimi, paste the API key into the masked input, and select a
model. Awesome validates the key before saving it. Use `/auth` later to add,
replace, remove, or select a credential source. Never paste an API key into a
chat message or slash-command argument.

For official key pages, Kimi China/global selection, billing/network
prerequisites, and the Provider data boundary, read
[What You Need](README.md#what-you-need) before entering a production key.

## 5. Verify Your Setup

Send a read-only request:

```text
Analyze this project's structure and tell me where I should start reading.
```

A successful answer confirms the Workspace, Thread, model, context, streaming,
and read-tool path without changing project files. Run `/context`, `/tools`,
and `/status` to inspect what Awesome used.

## Where to Go Next

- Learn the lifecycle in [Workspace, Thread, Turn, and Operation](../concepts/workspace-thread-turn.md).
- Choose an approval posture in [Permissions and safety](../user-guide/permissions.md).
- Learn the daily flow in the [User Guide](../user-guide/README.md).
- Diagnose a failed step with [Troubleshooting](../user-guide/troubleshooting.md).
