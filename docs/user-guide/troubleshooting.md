# Troubleshooting

## Unsupported host

V1 supports Apple Silicon macOS, Windows 11 x64, and WSL2 Ubuntu 24.04 x64.
Other hosts are rejected before installation writes the application.

## `awesome` is not found

Open a new terminal after installation. On macOS/WSL2 confirm `~/.local/bin` is
on PATH; on Windows confirm `%LOCALAPPDATA%\Programs\Awesome\bin` is on the
user PATH. Rerun the original installer only after closing all Awesome
processes.

## Git warning

Git is optional and is not installed by Awesome. Install it from the
[official Git site](https://git-scm.com/downloads) if the requested workflow
needs it.

## Provider credentials

Put `DEEPSEEK_API_KEY` or `MOONSHOT_API_KEY` in the process environment or
`<AWESOME_HOME>/.env`, not the workspace. If both exist, select a supported
full model ID with `/model` or user configuration. Restart after editing files.

## Workspace is not trusted

Restart `awesome` in the intended directory and verify the displayed canonical
path. A previous No is not persisted. Trust only paths whose files and
instructions you understand.

## Configuration is invalid

Run `/doctor` and inspect `<AWESOME_HOME>/config.yaml` plus the trusted
workspace `.awesome/config.yaml`. YAML must be a mapping with `version: 1`, no
duplicate/unknown keys, supported model IDs, and budgets within documented
limits.

## Core startup or version mismatch

Close all Awesome processes and rerun the original one-line installer. The
installer validates a fresh staged application before replacing the installed
app and preserves `<AWESOME_HOME>` user state. There is no rollback workflow.

## Mem0 or MCP is degraded

Use `/memory`, `/mcp`, `/config`, and `/doctor`. Check the required environment
variable is present and that the external command/network is available. These
extensions fail open: disable the affected capability and continue local work.
