# User Guide

This guide indexes user-facing runtime surfaces. Detailed pages can be split
out as those surfaces mature.

- Runs: `awesome-agent run`, `status`, `agents`, and `todos`.
- Team mode: `awesome-agent run --team`, child Runs, assignments, and mailbox.
- Approvals: `approve`, `resume`, and durable approval records.
- Extensions: project `skills/`, `awesome-agent.yaml`, and MCP sources.
- Diagnostics: `diagnostics`, `recovery-metrics`, `budget`, and
  `context-compactions`.

Start with the [quickstart](../getting-started/quickstart.md) before creating
real coding Runs.

## TUI Operator Console

Use `awesome-agent tui` when you want an interactive local view over active and
recent Runs. The console reads from the API and uses the same approval, cancel,
and resume endpoints as the CLI.

Useful keys:

| Key | Action |
| --- | --- |
| `r` | Refresh |
| `c` | Cancel selected Run |
| `u` | Resume selected Run |
| `a` | Approve latest pending approval for selected Run |
| `d` | Deny latest pending approval for selected Run |
| `q` | Quit |
