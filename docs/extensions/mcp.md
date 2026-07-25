# MCP servers

Awesome supports local stdio Model Context Protocol servers as external tool
providers. MCP is appropriate when a capability must run in another process or
reuse an existing MCP implementation. It is not needed for static instructions
([Skills](skills.md)) or durable facts ([Memory](memory.md)).

MCP servers and their output are untrusted extensions. A connected server does
not gain workspace trust, permission-mode authority, or a route around the Tool
Executor. Every MCP invocation requires one-call approval, including in Full
access.

## Declare a user server

Add a server to `<AWESOME_HOME>/config.yaml`:

```yaml
version: 1
mcp_servers:
  - id: issue-tracker
    command: python
    args: ["-m", "my_issue_tracker_mcp"]
    env: ["ISSUE_TRACKER_TOKEN"]
    enabled: true
```

User servers are controlled only by their `enabled` field. `/mcp enable` and
`/mcp disable` intentionally reject a user server and direct you back to user
configuration. Restart Awesome after changing the declaration.

## Declare a workspace server

Add a declaration to `<workspace>/.awesome/config.yaml`:

```yaml
version: 1
mcp_servers:
  - id: repository-index
    command: python
    args:
      - -m
      - repository_index_mcp
      - --root
      - .
    env: ["REPOSITORY_INDEX_TOKEN"]
```

A workspace declaration has no `enabled` field. It is ignored before workspace
trust and becomes `enablement_required` afterward. Review the command, arguments,
and environment allowlist, then enable it for the current workspace:

```text
/mcp status repository-index
/mcp enable repository-index
```

Enablement is stored in Application SQLite with the workspace key, server ID,
and a hash of `id`, `command`, `args`, and sorted environment names. Any change
to that authority-bearing declaration invalidates the old approval and requires
enablement again.

Server IDs must begin with a lowercase letter and contain at most 64 lowercase
letters, digits, underscores, or hyphens. IDs must be unique across the combined
user and workspace declarations. `command` is a direct executable name or path,
not a shell expression; `args` is an argument array.

`env` contains names only. Names must match `[A-Z_][A-Z0-9_]*` and be unique.
The child receives a minimal platform environment (`PATH`, temporary-directory
and essential Windows process variables) plus those explicitly named values
from the Core process environment. A value stored only in Awesome's `.env` for
Provider credentials is not automatically exported to an MCP child.

## Operate servers

```text
/mcp                         # all status records
/mcp status                  # same snapshot form
/mcp status <id>
/mcp enable <workspace-id>
/mcp disable <workspace-id>
/mcp restart <id>
```

| State | Meaning |
| --- | --- |
| `disabled` | User declaration has `enabled: false`. |
| `untrusted` | Workspace declaration exists but the workspace is not trusted. |
| `enablement_required` | Trusted workspace declaration has no matching current config-hash approval. |
| `configured` | Effective declaration is eligible but not currently connected. |
| `connected` | The Manager owns a live client plus one complete compiled catalog generation. Registry synchronization is a subsequent Application step. |
| `error` | Connection, catalog, call, or cleanup failed and the Manager catalog is invalid. The Application removes the matching namespace when it synchronizes or receives the bound invalidation callback. |

`/mcp restart <id>` first drops the old client and catalog, which removes the
old `mcp.<server-id>.*` registry namespace, then performs a fresh connection.
There is no lazy reconnect inside `call_tool()`. Reconnection happens only
during Turn preparation or an explicit restart flow, so an uncertain call is
never silently replayed.

## Catalog and Registry publication

Awesome does not register tools page by page:

```text
spawn + initialize stdio client
  -> list every catalog page
  -> enforce page/tool/byte limits
  -> compile every input and output JSON Schema
  -> Manager atomically publishes client + catalog generation + CONNECTED
  -> Application builds every RegisteredTool for that generation
  -> Registry atomically replaces the complete server namespace
```

If any tool name, contract, schema, page cursor, or resource limit is invalid,
the new client closes, the Manager catalog is invalidated, and status becomes
`error`. On synchronization, the Application removes the server namespace. A
valid subset of a Manager catalog or Registry namespace is never published.

The two atomic replacements are not one cross-component transaction.
`connected` describes Manager state and is not, by itself, proof that the
namespace was installed. There is also a current contract gap: the catalog
accepts an upstream component tool name of unbounded length, while a complete
namespaced name is limited to 128 characters in `/tools` payloads and 200 in
model/event contracts. An overlong valid-looking name can therefore leave the
Manager `connected` while Registry adaptation, model exposure, or `/tools`
presentation fails. Treat that state as unavailable, correct the server
catalog, and restart; runtime hardening should validate the complete
`mcp.<server>.<tool>` name against the strictest downstream consumer during
catalog compilation.

Catalog limits are:

| Resource | Limit |
| --- | ---: |
| Pagination pages | 128 |
| Tools per server | 128 |
| One input or output schema | 256 KiB |
| Complete catalog | 1 MiB |
| Schema nesting | 64 levels |
| Tool description | 500 characters |

Input and output schemas default to JSON Schema Draft 2020-12. An explicit
`$schema` may select a dialect supported by the installed `jsonschema` runtime.
Standard composition, conditionals, ranges, patterns, arrays,
`additionalProperties`, and `unevaluatedProperties` are enforced. `format`
keeps JSON Schema's default annotation semantics. Awesome does not install a
`FormatChecker` for any supported dialect, so formats are not asserted during
MCP argument or result validation.

`$ref`, `$dynamicRef`, and `$recursiveRef` are preflighted. References may only
target a fragment in the same schema resource. Missing fragments, remote
references, duplicate anchors or resource IDs, unknown dialects, and required
unknown vocabularies reject the catalog. Schema compilation never fetches the
network.

## Generation-bound invocation

Every successful catalog receives a generation. Registry handlers capture that
generation and the corresponding validators. Before a call, the Manager checks
that the server is connected, the tool remains present, and the captured
generation equals the current catalog.

```text
model arguments
  -> compiled input validator
  -> Tool Executor permission prompt
  -> Manager generation check
  -> exactly one remote call
  -> output resource preflight
  -> declared outputSchema validation
  -> bounded normalized ToolResult
```

Input validation happens before approval and before remote I/O. Validation
errors become a generic `invalid_arguments` result without echoing the original
arguments or schema.

If a server declares `outputSchema`, successful `structuredContent` is required
and must validate. Structured JSON is preflighted before schema traversal at
64 KiB, 4,096 nodes, and 64 levels. Responses may contain at most 1,024 content
blocks. Rendered text is bounded to 30,000 characters, retaining a head and tail
with an explicit omitted-character marker when needed. Invalid or missing
structured output becomes a sanitized, non-retryable execution failure.

## Timeout, cancellation, and uncertain outcome

Initialization and catalog listing use 30-second deadlines, and connection
cleanup is bounded to five seconds. A tool call has a 30-second Manager deadline
and a 40-second Tool Executor outer guard.

After a remote call starts, timeout or transport loss cannot prove whether the
server acted. Awesome therefore:

1. cancels local waiting and bounds connection cleanup;
2. invalidates the client, catalog generation, and registry namespace;
3. marks the server `error`;
4. returns non-retryable `uncertain_outcome` with explicit retry/abort recovery
   choices;
5. never reconnects or replays the call in the same Turn.

User cancellation performs the same invalidation and bounded cleanup, then
continues propagating the original cancellation. On recovery, uncertain
external work defaults to Abort before Retry because repeating it could repeat
a side effect. See [changes and recovery](../concepts/changes-and-recovery.md).

## Security and operational tradeoffs

- The MCP child is a host process, not an OS sandbox. Review the executable and
  its dependencies before enabling it.
- The environment allowlist reduces accidental secret inheritance; it does not
  constrain files or network resources the child can access under the current
  OS account.
- MCP annotations are presentation metadata, not authority. Awesome currently
  classifies MCP tools as `mcp.invoke`, non-read-only, and always asks.
- Atomic catalogs sacrifice partial availability for a coherent contract: the
  model can never see half of a server's schema generation.
- No transparent retry sacrifices convenience for at-most-one-call behavior at
  the Awesome boundary.

For failures, run `/mcp status <id>`, correct the declaration or server catalog,
then use `/mcp restart <id>`. Do not retry a timed-out mutating tool until you
have checked the remote system for the first call's effect.
