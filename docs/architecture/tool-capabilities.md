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

## Extension Tools

Built-in, MCP, skill, and community tools should flow through the same
capability and executor contracts. Extension discovery may add tools to the
catalog, but it must not bypass runtime authorization.

## Related Documents

- [Extensions](extensions.md)
- [Security model](security-model.md)
- [Agent loop](agent-loop.md)
