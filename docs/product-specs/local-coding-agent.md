# Local Coding Agent

## User Outcome

A user can submit a coding task locally, observe how the Leader plans it, see
which Teammates and Subagents are created, inspect tool results and task
progress, approve dangerous commands, and receive only independently verified
team output.

## V1 Capabilities

- solo read-only and solo modifying execution
- explicit scoped team runtime with Leader, Teammates, Verifier, Subagent
  lineage, scoped tools, verification rejection, and rework
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
- provider-neutral streamed messages, native tool calls, reasoning, stop
  reasons, usage, continuation, and classified model failures
- checkpointed Solo read-only model/tool loop with bounded repository
  inspection, correction feedback, final result projection, and minimal Todo
- checkpointed Solo modifying model/tool loop with patch application, final
  diff inspection, Docker-only allowed shell commands, validation gates, and
  bounded rework before validated completion or terminal failure
- durable exact-invocation approval interrupt/resume for ambiguous modifying
  shell commands, with CLI/API list and decide support
- durable active cancellation for queued, waiting, claimed, and executing solo
  Runs, including graph-task cancellation and cancellable subprocess/Docker
  shell boundaries
- frontend-ready Run, Agent, and Todo lifecycle projections with matching
  events, revision increments, and timestamps
- real team-runtime E2E evidence through Worker, PostgreSQL, checkpoint,
  provider protocol, tools, validation records, and observability records

## Non-Goals

- production deployment
- production frontend
- multi-user authentication
- multiple active model providers
- recursive delegation
- automatic solo/team routing
- distributed Teammate child Runs claimed by independent Workers
- LangSmith or LangGraph Agent Server
