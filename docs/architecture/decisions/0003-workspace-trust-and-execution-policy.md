# ADR 0003: Workspace Trust and Execution Policy

- Status: Accepted
- Date: 2026-07-10
- Scope: Workspace, permission, approval, and sandbox boundaries

## Context

A local coding agent needs broad access to the project it is asked to modify,
but repository instructions and paths are untrusted until the developer accepts
the workspace. Per-tool approval matrices create friction and do not provide a
real operating-system sandbox.

## Decision

The canonical startup directory is the workspace. The first visit requires an
explicit trust decision stored in user-owned state. Declining trust exits before
project instructions, skills, MCP declarations, or tools are loaded.

Trust grants normal read, write, edit, delete, and development-command work
inside that workspace. File tools still enforce canonical path containment and
safe symlink handling. Tool policy is enforced at the executor boundary.

The generalized durable approval subsystem is removed. Normal in-workspace
operations do not prompt per tool. A small `interaction_required` protocol may
ask allow-once or deny for workspace trust and exceptional boundary crossings;
it is not an approval resource or configurable approval mode system.

Local host execution is the first backend. Docker may later be added as an
optional execution sandbox backend below the tool executor. Docker is not the
product runtime, and no Docker backend is built in the first phase.

## Consequences

- Workspace configuration cannot grant its own trust or expand user policy.
- The product must describe host execution honestly until isolation exists.
- Security-critical path and execution checks remain code invariants even in a
  trusted workspace.
- Git worktrees may support workflows, but they are not called a sandbox.

## Rejected Alternatives

- Trust every current directory implicitly: allows unreviewed project content
  to influence the agent.
- Prompt for every modifying tool: poor local coding workflow and no material
  containment benefit.
- Require Docker: increases startup cost and excludes normal host development
  workflows.
