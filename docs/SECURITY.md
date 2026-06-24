# Security

- Docker is the default execution boundary.
- Host execution requires explicit CLI `--trusted-local` consent.
- FastAPI cannot select trusted-local execution.
- Tools use least-privilege capability grants.
- Commands are classified as `ALLOW`, `ASK`, or `DENY`.
- High-risk approvals are scoped to run, agent, command, workspace, and expiry.
- Writing Teammates use isolated Git worktrees.
- Subagents cannot delegate or authorize actions.
- Secrets are redacted before persistence, telemetry, artifacts, and memory.
- Mem0 content is untrusted external context.
- Full source, full conversations, and raw tool output are excluded from memory.

