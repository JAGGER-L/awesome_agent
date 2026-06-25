# Local Coding Agent

## User Outcome

A user can submit a coding task locally, observe how the Leader plans it, see
which Teammates and Subagents are created, inspect tool results and task
progress, approve dangerous commands, and receive only independently verified
team output.

## V1 Capabilities

- solo and team execution
- dynamic task tree
- Teammate mailbox
- bounded Subagent delegation
- mandatory team verification
- Docker command execution
- PostgreSQL resume
- durable PostgreSQL API projections across service restarts
- traceable conversations, tools, artifacts, and approvals
- configurable per-role model assignments, exposed for inspection
- optional built-in and Mem0 memory
- Typer CLI and local FastAPI inspection API
- local allowed-root configuration and PostgreSQL repository registry
- clean-base read-only/modifying Run intake into stable named worktrees
- durable `created + queued` intake with crash reconciliation
- PostgreSQL claim, lease, heartbeat, fencing, retry, and expiry recovery
- read-only dispatch inspection API
- one-Run-per-process durable Worker for diagnostic runtime probes
- LangGraph checkpoint resume after Worker process failure
- local API/Worker supervisor and PostgreSQL-backed cross-process SSE

## Non-Goals

- production deployment
- production frontend
- multi-user authentication
- multiple active model providers
- recursive delegation
- LangSmith or LangGraph Agent Server
- Coding Run execution until the model/tool loop is implemented
