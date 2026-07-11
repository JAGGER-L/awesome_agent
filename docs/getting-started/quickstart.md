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
