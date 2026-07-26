# Extend Awesome

This section is for users and maintainers who want Awesome to remember stable
facts, follow a reusable workflow, or call an external tool server. Read the
[core concepts](../concepts/README.md) and trust the workspace before enabling
workspace-controlled extensions.

Awesome has three extension families. They solve different problems and have
different authority, persistence, and privacy boundaries; none is a general
plugin system.

## Choose the smallest extension that fits

| Need | Use | What enters a Turn | Where it lives | Network | Permission effect |
| --- | --- | --- | --- | --- | --- |
| Reuse stable preferences or project facts | [Memory](memory.md) | Bounded, untrusted reference context | User-owned Markdown, optionally Mem0 Cloud | Local: no; Mem0: yes | Cannot grant tool permission |
| Reuse instructions and supporting text files | [Skills](skills.md) | One selected instruction body or lazily read resources | Bundled, user, or trusted workspace package | No, unless the Skill later asks the Agent to use a networked tool | Cannot grant tool permission; `allowed-tools` is descriptive |
| Add tools implemented by another process | [MCP](mcp.md) | Validated tool schemas and bounded results | User or trusted workspace configuration | Server-defined | Every call asks once, even in Full access |

Use Memory for facts, not procedures. Use a Skill for a procedure, not durable
state. Use MCP only when the capability must cross the Core process boundary.
Combining all three for a simple convention increases context, failure modes,
and trust surface without adding value.

## The invariant all extensions share

Project-controlled content may influence the model only after workspace trust,
and model influence is never authority. Tool calls still pass through the same
registry, argument validation, permission policy, timeout, audit, and event
pipeline as built-in tools.

```text
user config / trusted workspace config / package files
                         |
                         v
          configuration load, Skill discovery, or catalog compile
                         |
                         v
              context source or ToolSpec
                         |
                         v
                    Agent Loop
                         |
                         v
       Tool Executor -> permission -> backend-specific limits
                         |
                         v
              normalized result + audit
```

This design deliberately separates four questions:

1. **Trust:** may files from this workspace be read as configuration or
   instructions?
2. **Validity:** can the content be parsed within the product's resource
   limits?
3. **Relevance:** should the content enter this Turn?
4. **Authority:** may the requested tool operation execute?

Passing an earlier question never implies passing a later one. For example, a
trusted workspace MCP declaration still needs explicit enablement, a valid
catalog, and one-call approval.

## Configuration and inspection

- User declarations live under `<AWESOME_HOME>` and apply across workspaces.
- Workspace declarations live under `<workspace>/.awesome/` and are ignored
  until trust is accepted.
- `/memory`, `/skills`, and `/mcp` show their respective runtime state.
- `/tools` shows the effective tool catalog and whether the current permission
  mode would ask.
- `/doctor` reports configuration and Provider readiness; extension-specific
  diagnostics remain visible in their own command output.

The complete YAML contract is in the
[configuration reference](../reference/configuration.md). Exact tool and
permission behavior is in [built-in tools](../reference/built-in-tools.md) and
[permission modes](../reference/permission-modes.md).

## Failure isolation

Extensions are optional to the Agent Loop:

- one invalid Skill produces a diagnostic without hiding valid Skills;
- invalid or unreadable enabled **local** Memory fails Turn context preparation
  rather than silently omitting durable facts; the coordinator terminalizes
  the already-created Turn and attempts to remove its checkpoint, while startup
  reconciliation retries removal after a cleanup failure;
- unavailable **Mem0 Cloud** recall omits that cloud source and reports a
  bounded diagnostic;
- an invalid MCP catalog, failed publication, or failed MCP transport
  invalidates that server's Manager snapshot and Registry namespace without
  replacing Core state or another server's tools.

These boundaries are not uniform. In particular, the current workspace
configuration reader is trust-gated but does not yet have the bounded,
no-follow, post-open identity checks used for `AGENTS.md` and Workspace Skills.
For MCP, one server lock protects candidate compilation and publication: the
Registry replaces the complete namespace first, then the Manager publishes the
same generation and `CONNECTED` without an intervening `await`. Shared Registry
limits can reject the whole candidate without disturbing another namespace.
See [configuration](../reference/configuration.md) and [MCP](mcp.md).

Optional does not mean invisible. A degraded extension can change the evidence
available to the model, so diagnose it rather than assuming a Turn used the
same context and tools as an earlier run.

## Recommended reading paths

- Personal preferences or repository conventions: [Memory](memory.md) ->
  [context and instructions](../concepts/context-and-instructions.md).
- Repeatable review, debug, or test procedure: [Skills](skills.md) ->
  [tools and shell](../user-guide/tools-and-shell.md).
- External tool server: [MCP](mcp.md) ->
  [permissions](../user-guide/permissions.md) ->
  [Protocol v3](../reference/protocol.md).
