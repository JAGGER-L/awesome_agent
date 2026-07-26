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
| `connected` | The live client, complete compiled catalog generation, and complete Registry namespace are all published. |
| `error` | Connection, catalog, publication, call, or cleanup failed; the Manager catalog is invalid and that server's Registry namespace is absent. |

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
  -> compile every input/output JSON Schema and final namespaced name
  -> build every generation-bound RegisteredTool candidate
  -> Registry validates its aggregate snapshot and atomically replaces namespace
  -> without awaiting, Manager commits generation + client + catalog + CONNECTED
```

The complete candidate is compiled while the Manager holds that server's lock.
Registry replacement is an all-or-none synchronous operation. After it
succeeds, no `await` separates the Manager assignments that publish the same
generation and finally set `CONNECTED`. Therefore `connected` means both the
Manager catalog and that generation's complete namespace are available; it is
not an intermediate catalog-only state.

If any tool name, contract, schema, page cursor, per-server bound, or shared
Registry bound is invalid, the new client closes, the Manager invalidates the
catalog generation and removes that server namespace, and status becomes
`error` with a fixed, sanitized diagnostic. No valid subset is published, and
another server's committed namespace remains intact. The compiler validates the
complete `mcp.<server>.<tool>` name against the 128-character downstream limit,
so an overlong name fails before Registry publication.

Catalog limits are:

| Resource | Limit |
| --- | ---: |
| Pagination pages | 128 |
| Tools per server | 128 |
| One input or output schema | 256 KiB |
| Complete catalog | 1 MiB |
| Complete schema JSON nesting | 64 levels |
| Complete `mcp.<server>.<tool>` name | 128 characters |
| Tool description | 500 characters |

The shared Tool Registry also enforces aggregate limits across built-ins and
all extension namespaces: at most 128 tools and at most 1 MiB for the canonical
model-facing definitions (`name`, `description`, and `input_schema`). These are
whole-Registry budgets, not additional per-server allowances. A candidate that
fits its server catalog can therefore still be rejected as a whole when the
effective shared Registry would exceed either budget.

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
network. Before dialect, semantic, or reference traversal, the 64-level limit
is applied to the complete schema JSON tree, including `default`, `examples`,
and unknown extension-keyword values.

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
structured output, and any text block that is not valid UTF-8, becomes a
sanitized, non-retryable execution failure before rendering.

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
