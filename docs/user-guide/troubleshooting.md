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

Environment and Awesome-managed credentials are separate sources. `/auth`
shows which sources exist and which one is selected. Environment is read-only
in the TUI; update or remove it in the launching shell. If the selected source
becomes unavailable, Awesome reports that state and waits for a new selection
instead of silently using the other source. As an advanced option, edit
`<AWESOME_HOME>/.env` and restart Awesome. Run `/doctor` when you need an
on-demand Provider network check; startup itself performs only a presence
check.

## Workspace is not trusted

Restart `awesome` in the intended directory and check the displayed canonical
path. Choosing No does not save a denial, so Awesome asks again on the next
launch. Trust only projects whose files and instructions you understand.

## `AGENTS.md` was ignored

Read the full reason in Welcome, the status line, or `/doctor`. The root file
must be a plain, stable, UTF-8 text file with no NUL bytes, links, junctions, or
other reparse components. It must fit both the 32 KiB byte limit and its context
allocation. Awesome ignores an invalid file whole; fix it and start a new
session to take a new immutable snapshot. A missing `AGENTS.md` is normal.

## Configuration is invalid

Run `/doctor` and inspect `<AWESOME_HOME>/config.yaml` together with the trusted
workspace `.awesome/config.yaml`. YAML must be a mapping with `version: 1`, no
duplicate or unknown keys, supported model IDs, and budgets within documented
limits.

## Core startup or version mismatch

Close every Awesome process and rerun the original one-line installer. It
stages and validates the new application before replacing the installed files.

## Awesome asks to reset local state

After an incompatible local data-format change, Awesome can offer to reset
conversation state before opening the workspace. Review the confirmation panel
and choose `Reset local state and continue` only when you accept losing local
conversations, workspace trust, checkpoints, and undo history. API keys,
configuration, Skills, and Local or Cloud Memory settings are preserved.

Choose Exit or press Esc to leave the state unchanged. A successful reset
continues to workspace trust without restarting Awesome. If reset reports that
another session is using the state, close other Awesome processes and retry.

State created by a newer Awesome version is never reset by an older binary;
rerun the normal installer to upgrade. Unknown, corrupt, unreadable, or locked
state produces a diagnostic instead of a destructive prompt. Do not delete the
data directory manually to bypass those diagnostics.

## Input is shown as pending

Awesome runs one foreground task at a time and keeps up to three later inputs
in a session-only queue. Wait for the current task to finish, or cancel it with
Ctrl+C; the next input then starts automatically. With an empty input box, press
Up to recall the newest pending item into the draft. A full queue or a queued
`/quit` keeps new text in the input box and explains why it was not queued.

## Mem0 Cloud or MCP is unavailable

Use `/memory`, `/mcp`, `/config`, and `/doctor`. Confirm the required environment
variable is present and the configured external command or network is
available. Disable the affected extension to continue working while the
external service is unavailable. MCP timeout or connection loss may mean the
remote side already acted; Awesome invalidates that catalog and reports an
uncertain outcome instead of reconnecting and replaying the call automatically.
