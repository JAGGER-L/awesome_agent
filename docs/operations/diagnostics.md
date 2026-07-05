# Diagnostics

## Local CLI Check

Use `awesome doctor` before the first model-backed local CLI session when setup
is unclear.

## API And Runtime Readiness

Use these commands when operating the API modes:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod "http://127.0.0.1:8000/ready?profile=api"
Invoke-RestMethod "http://127.0.0.1:8000/ready?profile=runtime"
awesome-agent doctor --profile api
awesome-agent doctor --profile runtime
```

`/health` is process liveness. `/ready` and `doctor` inspect dependencies such
as configuration, database, migrations, checkpoint storage, sandbox health,
provider configuration, extension catalog health, and Worker heartbeat.

## What To Capture

When reporting a runtime problem, capture the command, profile, request id,
thread id, run id, approval id when present, and the first failing readiness
component. Do not paste secrets or full raw logs into tracked docs.

## Logs

```powershell
docker compose logs api
docker compose logs worker
docker compose logs sandbox
```

Keep the local API on loopback unless an external authentication and network
boundary is added.

## Team Tree Diagnostics

Use `GET /runs/{run_id}/team/tree` or `awesome team-tree <run_id>` to inspect
Leader, Teammate, Subagent, and Verifier state. Prefer the tree over raw event
order when diagnosing child waits, verifier rework, patch aggregation, and why
a team run is paused or still active.
