# Security

- Docker is the default execution boundary.
- Host execution requires explicit CLI `--trusted-local` consent.
- FastAPI cannot select trusted-local execution.
- Tools use least-privilege capability grants.
- Commands are classified as `ALLOW`, `ASK`, or `DENY`.
- High-risk approvals are scoped to run, agent, command, workspace, and expiry.
- Writing Teammates use isolated Git worktrees or child Run workspaces.
- Subagents cannot delegate or authorize actions.
- Secrets are redacted before persistence, telemetry, artifacts, and memory.
- Mem0 content is untrusted external context.
- Full source, full conversations, and raw tool output are excluded from memory.
- Visible provider reasoning is excluded from memory. It may later be redacted
  and stored as bounded model-call evidence or an artifact for user inspection.
- Private model continuation is checkpoint-only and excluded from APIs,
  frontend events, logs, runtime events, artifacts, and memory.
- Context compaction artifacts can contain raw removed messages or full tool
  observations. Treat artifact storage as sensitive execution evidence, not as
  a sanitized public cache. Deterministic summaries are for prompt budgeting
  and audit navigation; they are not a redaction boundary.
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
- Allowed roots default to an empty deny-all list and are changed only by local
  CLI/configuration, not FastAPI.
- Registration rejects bare repositories and linked worktrees. Run intake
  rejects dirty repositories, untracked files, and in-progress Git operations.
- Read-only and modifying Runs use isolated named worktrees from an exact clean
  Git base. Trusted-local changes the execution backend but never authorizes
  direct edits to the user's checkout.
- Protected worker writes require the current worker UUID and fencing token.
  Stale or expired owners cannot heartbeat, append protected events, or change
  dispatch state.
- Claim and heartbeat are internal PostgreSQL operations, not public FastAPI
  endpoints.
- The local FastAPI inspection API is unauthenticated and binds to loopback by
  default. CLI `serve` and `start` reject non-loopback hosts unless the user
  passes `--unsafe-bind-public` explicitly. The CLI also propagates
  `AWESOME_AGENT_API_HOST` and `AWESOME_AGENT_UNSAFE_BIND_PUBLIC` into the API
  process environment.
- Direct ASGI hosting uses the same settings-driven bind policy. Setting
  `AWESOME_AGENT_API_HOST` to a non-loopback host without
  `AWESOME_AGENT_UNSAFE_BIND_PUBLIC=true` rejects API startup. Public binding
  remains unauthenticated and is intentionally labeled unsafe.
- `/health` and `/ready` expose dependency state only; they do not grant tool,
  repository, or worker control. Operators should still keep the local API on
  loopback unless they add an external authentication boundary.
- The `runtime_probe` graph has no model, tool, shell, sandbox, or
  repository-content capability.
- `POST /runtime/probes` selects the fixed supported probe graph on the server;
  callers cannot supply arbitrary graph names or versions.
- `solo-readonly@1` exposes only bounded `status`, `list`, literal `search`,
  `read`, and instruction-discovery tools against the managed Run worktree.
- Read tools reject absolute/parent paths, `.git`, symlink or junction
  traversal, binary files, and common credential/private-key files.
- Workers without a configured model API key do not claim model-driven solo or
  scoped team Coding Runs. They may still claim deterministic distributed team
  child-run graphs that do not call a provider.
- Distributed team assignments carry explicit `allowed_tools`,
  `allowed_skills`, write permission, delegation permission, and Subagent
  limits. Child Runs must not register or execute capabilities outside their
  assignment.
- Team mailbox messages are route-restricted durable records, not arbitrary
  cross-agent chat. Subagents report to their owning Teammate and do not read
  the team mailbox.
- Workspace cleanup validates resolved ownership markers, managed-root
  containment, active leases, branch identity, and unexported diffs before
  deletion. It defaults to preview and requires explicit apply.
- Failed or dirty workspace cleanup requires force with a reason.
  `recovery_required` workspaces cannot be removed by ordinary force.
- V1 approvals bind to one exact tool invocation, canonical arguments,
  workspace, capabilities, risk, and expiry. Default expiry is 60 minutes and
  configurable up to 24 hours.
- `.agents/validation.toml` and project metadata are untrusted repository input.
  Only strongly evidenced check-only commands may run automatically; ambiguous
  or side-effecting commands require approval.
