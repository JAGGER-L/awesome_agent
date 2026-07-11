# Memory, Skills, and MCP

These capabilities extend context and tools; none can grant workspace trust,
change system policy, or bypass the Tool Executor.

## Local file memory

Local memory is disabled by default and uses two user-owned Markdown files:

- `<AWESOME_HOME>/memory/USER.md` for cross-workspace preferences;
- `<AWESOME_HOME>/workspaces/<workspace_key>/MEMORY.md` for one canonical
  workspace.

Use `/memory local on` or `/memory local off` independently of cloud memory.
`/memory list <user|workspace>`, `add`, `replace`, and `remove` provide explicit
bounded mutation rather than allowing arbitrary prompt writes.

## Mem0 Cloud

Mem0 Cloud is the only external memory Provider in V1. It is independently
disabled by default and requires `MEM0_API_KEY` in the process environment or
`<AWESOME_HOME>/.env`.

Use `/memory mem0 on`, `/memory mem0 off`, `/memory mem0 search <query>`, and
`/memory mem0 remove <id>`. The Core creates an opaque user ID and uses an
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

`/skills` lists the catalog. `/skill` shows or selects `auto`, `off`, or one
named Skill for the thread. Skill bodies and resources are loaded on demand;
they are not injected into every turn. Bundled `/init`, `/review`, `/debug`,
`/test`, and `/commit` commands select a Skill and submit a normal task.

## MCP

MCP server declarations live in user or trusted workspace configuration. User
servers have an explicit `enabled` flag; workspace declarations become eligible
only after trust. Each declaration contains an ID, command, arguments, and an
allowlist of environment variable names—never embedded secret values.

MCP tools are registered under namespaced names and use the normal Tool
Executor. `/mcp` reports connected/degraded state. A failing server degrades
that extension and reports diagnostics without replacing Core or changing the
workspace boundary.
