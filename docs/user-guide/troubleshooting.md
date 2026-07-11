# Troubleshooting

## `awesome` is not found

Open a new terminal after installation. On macOS or WSL2, confirm
`~/.local/bin` is on PATH. On Windows, confirm
`%LOCALAPPDATA%\Programs\Awesome\bin` is on the user PATH. Close every Awesome
process before running the installer again.

## Git warning

Git is optional and is not installed by Awesome. Install it from the
[official Git site](https://git-scm.com/downloads) when the requested workflow
needs it.

## Model credentials

Put `DEEPSEEK_API_KEY` or `MOONSHOT_API_KEY` in the process environment or
`<AWESOME_HOME>/.env`, not in the workspace. If both exist, select a supported
full model ID with `/model` or user configuration. Restart Awesome after editing
environment files.

## Workspace is not trusted

Restart `awesome` in the intended directory and check the displayed canonical
path. Choosing No does not save a denial, so Awesome asks again on the next
launch. Trust only projects whose files and instructions you understand.

## Configuration is invalid

Run `/doctor` and inspect `<AWESOME_HOME>/config.yaml` together with the trusted
workspace `.awesome/config.yaml`. YAML must be a mapping with `version: 1`, no
duplicate or unknown keys, supported model IDs, and budgets within documented
limits.

## Core startup or version mismatch

Close every Awesome process and rerun the original one-line installer. It
stages and validates the new application before replacing the installed files.

## Mem0 Cloud or MCP is unavailable

Use `/memory`, `/mcp`, `/config`, and `/doctor`. Confirm the required environment
variable is present and the configured external command or network is
available. Disable the affected extension to continue working while the
external service is unavailable.
