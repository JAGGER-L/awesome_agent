# Troubleshooting

## API key is missing

Set `AWESOME_AGENT_DEEPSEEK_API_KEY` in the OS environment or
`<AWESOME_HOME>/.env`, then restart your terminal.

## `awesome` command not found

Return to the Awesome checkout and run:

```powershell
make install
```

Open a new terminal and try:

```powershell
awesome --help
```

## Docker is not running

Start Docker Desktop, wait until it is ready, then rerun the Docker command.

## API mode is unhealthy

Check [diagnostics](diagnostics.md), then inspect API, Worker, and sandbox logs.
If migrations fail on a fresh database, check
[technical debt](../governance/technical-debt.md) for known migration issues.

## Approval Required Repeats

A repeated approval prompt means the runtime believes the resumed operation is
not the same canonical tool invocation. Check approval id, tool call id,
arguments hash, workspace fingerprint, and capability list in runtime events.

## No Resumable Turn

`no_resumable_turn` means the current thread has no run in a paused, waiting,
retryable, or recoverable state.

## Resumable Run Changed

`resumable_run_changed` means the client tried to continue a run id that is no
longer the latest resumable run for the thread. Refresh thread run state and
continue the current run.

## Provider Timeout

Provider timeout indicates the model worker did not produce events within the
configured first-event, idle, or total timeout. Check provider configuration,
API key environment variables, and model availability.
