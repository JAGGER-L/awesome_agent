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
