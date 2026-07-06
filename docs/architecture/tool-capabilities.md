# Tool Capabilities

Tools are runtime capabilities, not prompt-only suggestions. A tool is visible
to a model only when the runtime grants it for the current route, role, and
assignment.

## Capability Model

Each tool has:

- a stable name and schema
- declared side-effect level
- route and role visibility rules
- approval requirements
- timeout and execution boundary behavior
- persisted result and replay semantics

## Execution Boundary

The model requests a tool call. The runtime validates canonical arguments,
capability grants, approval state, workspace fingerprint, and tool version
before execution. Side-effecting tools produce durable evidence.

## Approval Scope

Approval resume belongs to one canonical invocation. A resumed original call
must validate the schema version, argument hash, workspace path, workspace
fingerprint, and capability set before execution.

Repeated requests may reuse a prior decision only through bounded grant scopes:
exact `shell.execute` argv or exact `repo.apply_patch` target path set. Different
schema versions, workspaces, capabilities, risk levels, commands, patch paths,
or expired decisions must request a new approval.

## Extension Tools

Built-in, MCP, skill, and community tools should flow through the same
capability and executor contracts. Extension discovery may add tools to the
catalog, but it must not bypass runtime authorization.

Team role exposure and execution use the same runtime tool assembly. A tool may
not be treated as product-executable for a Teammate or Subagent unless the
runtime executor can resolve the same tool name, schema version, risk level,
and required capabilities that the exposure policy presents to the model.

When a team role calls a risky executable tool, approval binds to that exact
tool invocation. Approval resume validates the canonical arguments hash,
workspace fingerprint, tool version, and effective capabilities before replay;
it does not grant a session-wide permission.

## Related Documents

- [Extensions](extensions.md)
- [Security model](security-model.md)
- [Agent loop](agent-loop.md)
