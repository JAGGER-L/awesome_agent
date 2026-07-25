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

Workspace Skill discovery rejects a symlink, junction, or other reparse point
at the `.awesome`, `skills`, package, `SKILL.md`, or resource boundary. Core
revalidates directory and file identity when a body or resource is opened, so
replacing a package after discovery cannot redirect a read outside the
workspace. `SKILL.md` is bounded to 1 MiB, UTF-8, and non-binary content. One
bad package produces one diagnostic without hiding valid bundled, user, or
workspace Skills. These strict reparse rules apply to workspace-origin Skills;
they do not retroactively change existing user-level link behavior.

`/skills` opens the catalog and selects `auto`, `off`, or one named Skill for
the thread. You can also select directly with `/skills <name>`. Skill bodies
and resources are loaded on demand; they are not injected into every turn.
Repository initialization and other workflows use `/skills` or a
natural-language request; selecting a Skill never submits a hidden Agent Turn.

## MCP

MCP server declarations live in user or trusted workspace configuration. User
servers have an explicit `enabled` flag; workspace declarations become eligible
only after trust. Each declaration contains an ID, command, arguments, and an
allowlist of environment variable names—never embedded secret values.

MCP tools are registered under namespaced names and use the normal Tool
Executor and always require a one-call approval, including in Full access.
`/mcp` reports connected/degraded state; `/mcp restart <id>` removes the old
namespace before reconnecting.

Before a server becomes connected, Core compiles its entire tool catalog
atomically with JSON Schema Draft 2020-12 by default. Core follows pagination
until completion, rejecting cursor cycles and catalogs above 128 pages while
enforcing tool-count and byte limits during collection. Supported local schema
constraints include combinations, conditions, ranges, patterns, arrays, and
additional or unevaluated properties. `format` keeps JSON Schema's default
annotation behavior. References must be fragments inside the same schema;
remote references, missing fragments, unknown dialects or required
vocabularies, duplicate tool names, and resource-limit violations reject the
whole catalog without network resolution or partial registration. Limits are
128 tools per server, 256 KiB per schema, 1 MiB per catalog, and nesting depth
64. Connection, initialization, listing, and cleanup are deadline-bounded; a
late catalog result is never published after its deadline.

Arguments are checked with the compiled input validator before approval and
before any remote call. If a tool declares `outputSchema`, its
`structuredContent` must be present and match that schema before Awesome shows
the result. When a successful response has structured content but no text
content, Awesome renders deterministic bounded JSON; invalid, missing, or
unrepresentable output becomes a sanitized non-retryable failure instead of
leaking the original arguments or schema. Structured JSON is preflighted at
64 KiB, 4,096 nodes, and 64 levels; text and media results may contain at most
1,024 content blocks before rendering.

Every catalog and handler carries a generation. Restart, disconnect, timeout,
or cancellation invalidates the old generation, so an old validator cannot
call a replacement tool. Calls are never lazily reconnected, replayed, or
retried inside the same Turn. A 30-second MCP call deadline has a 40-second
outer backend guard; timeout or connection loss closes the connection and
returns a non-retryable uncertain-outcome result because the remote side may
already have acted. A failing server degrades that extension and reports a
bounded diagnostic without replacing Core or changing the workspace boundary.
