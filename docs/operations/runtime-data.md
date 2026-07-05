# Runtime Data

`AWESOME_HOME` defaults to `%LOCALAPPDATA%\awesome-agent` on Windows and
`~/.awesome-agent` on other platforms.

| Resource | Default | Purpose |
| --- | --- | --- |
| API port | `127.0.0.1:8000` local, `0.0.0.0:8000` inside Docker | Local inspection API. |
| PostgreSQL port | `54329` host, `5432` container | Durable runtime state for API modes. |
| AIO sandbox port | `127.0.0.1:8765` host, `8765` container | Sandbox service health and command execution. |
| Runtime data | `<AWESOME_HOME>/runs/` local, `/var/lib/awesome-agent/runs/` Docker | Per-run artifacts and runtime evidence. |
| Attachment data | `settings.local_state_dir / "attachments"` | Copied user input files bound to a turn. |
| Compose volume | `awesome_agent_runtime` | Container runtime state. |
| Compose user-data volume | `awesome_agent_user_data` | Model-visible workspace mounted into API, Worker, and sandbox. |

Thread attachments are user input, not generated artifacts. Deleting an
attachment physically removes stored content and leaves metadata needed for
auditability.

## Cleanup Boundary

Runtime data cleanup must not delete the user's project checkout. Remove only
state under `AWESOME_HOME`, Docker volumes created for Awesome Agent, retained
run worktrees explicitly owned by the runtime, or attachment/artifact stores
identified by diagnostics.
