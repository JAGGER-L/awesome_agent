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

If Awesome starts without a configured model, press Enter or run `/model`,
choose DeepSeek or Kimi, and paste the key into the masked input. Use `/auth`
to replace or remove a saved key. Invalid keys are not stored; network or
Provider failures offer an explicit `Save anyway` choice.

A process-environment credential overrides Awesome's user secret file and is
read-only in the TUI. Update or remove it in the launching shell instead. As an
advanced fallback, edit `<AWESOME_HOME>/.env` and restart Awesome. Run `/doctor`
when you need an on-demand Provider network check; startup itself performs only
a presence check.

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
