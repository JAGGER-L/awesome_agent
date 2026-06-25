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
- DeepSeek and Mem0 credentials are read from the ignored local `.env` or the
  process environment and are never committed, logged, or persisted in memory.
- `.env.example` contains names and non-secret defaults only.
- `.codex/` contains local development-agent plans and is ignored.
- `.agents/` contains tracked runtime-agent configuration only; secrets and
  generated run state are forbidden there.
- `.awesome-agent/` contains ignored local runtime data. Durable runtime state
  belongs in PostgreSQL.
- API run creation accepts a registered repository ID, not an arbitrary local
  path.
- PostgreSQL repository registration does not grant access by itself; resolved
  roots must also be contained by locally configured allowed roots.
- Modifying Runs use isolated worktrees from a clean Git base. Trusted-local
  changes the execution backend but never authorizes direct edits to the user's
  checkout.
- Worktree cleanup validates resolved ownership, active leases, and unexported
  diffs before deletion.
- V1 approvals bind to one exact tool invocation, canonical arguments,
  workspace, capabilities, risk, and expiry. Default expiry is 60 minutes and
  configurable up to 24 hours.
- `.agents/validation.toml` and project metadata are untrusted repository input.
  Only strongly evidenced check-only commands may run automatically; ambiguous
  or side-effecting commands require approval.
