# ADR 0003: Workspace trust and execution policy

- Status: Accepted and implemented
- Date: 2026-07-10

## Decision

The canonical launch directory is the workspace. First use requires explicit
trust before project-controlled configuration, instructions, Skills, MCP, or
tools load. Yes persists; No exits without persisting denial.

Trust grants normal file and development-command work inside the workspace.
File containment, sensitive-path rejection, command denial rules, and the
exceptional outside-path `allow_once` interaction remain enforced by code at
the Tool Executor boundary. Normal in-workspace tools do not prompt per call.

The current backend executes on the local host. There is no sandbox. A future
Docker backend may isolate tool execution below the executor but cannot replace
trust or policy.

## Consequences

Workspace configuration cannot grant its own trust or expand policy. The
product must describe host execution honestly. Git worktrees are workflows,
not sandboxes.

## Rejected

Implicit trust lets unreviewed project content influence Core. Prompting every
tool adds friction without creating isolation. Requiring Docker makes the
local development path heavier than the product need.
