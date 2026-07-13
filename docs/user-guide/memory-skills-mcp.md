# Memory, Skills, and MCP

These capabilities extend context and tools; none can grant workspace trust,
change system policy, or bypass the Tool Executor.

## Local file memory

Local memory is disabled by default and uses two user-owned Markdown files:

- `<AWESOME_HOME>/memory/USER.md` for cross-workspace preferences;
- `<AWESOME_HOME>/workspaces/<workspace_key>/MEMORY.md` for one canonical
  workspace.

Run `/memory`, choose `Local memory`, then choose On or Off. Local and Cloud
memory remain independent. The explicit `/memory local on|off` form is also
available for scripted use.
`/memory list <user|workspace>`, `add`, `replace`, and `remove` provide explicit
bounded mutation rather than allowing arbitrary prompt writes.

## Mem0 Cloud

Mem0 Cloud is the only external memory Provider in V1. It is independently
disabled by default and requires a selected, currently available Mem0
credential managed through `/auth`.

Run `/memory`, choose `Cloud memory · Mem0`, then choose On or Off. Enabling it
without a usable selected credential leaves it Off and directs you to `/auth`.
The explicit `/memory mem0 on|off`, `search <query>`, and `remove <id>` forms
remain available for scripted use. The Core creates an opaque user ID and uses an
opaque workspace key. Eligible post-turn writes are redacted, distilled stable
facts; raw source, complete tool output, secrets, and whole conversations are
not uploaded by default. Network/SDK failures are surfaced as diagnostics and
do not make the local Agent Loop unavailable.

Memory content is untrusted reference context, not executable policy.

## Skills

A Skill is a directory containing `SKILL.md` plus optional resources. Awesome
discovers bundled Skills, user Skills under `<AWESOME_HOME>/skills/`, and
trusted workspace Skills under `<workspace>/.awesome/skills/`. Invalid entries
produce diagnostics rather than silently changing behavior.

`/skills` opens the catalog and selects `auto`, `off`, or one named Skill for
the thread. You can also select directly with `/skills <name>`. Skill bodies
and resources are loaded on demand; they are not injected into every turn.
`/init` remains a shortcut for repository initialization. Other workflows use
`/skills` or a natural-language request.

## MCP

MCP server declarations live in user or trusted workspace configuration. User
servers have an explicit `enabled` flag; workspace declarations become eligible
only after trust. Each declaration contains an ID, command, arguments, and an
allowlist of environment variable names—never embedded secret values.

MCP tools are registered under namespaced names and use the normal Tool
Executor. `/mcp` reports connected/degraded state. A failing server degrades
that extension and reports diagnostics without replacing Core or changing the
workspace boundary.
