# Extensions

Extensions let the product discover skills, MCP servers, and community tools
without hard-coding every capability into the core runtime.

## Sources

Extension inventory can come from:

- built-in package metadata
- user-level configuration under `<AWESOME_HOME>`
- project-level configuration in the current repository
- project `skills/`
- user `skills/`
- MCP server declarations

Provider keys remain outside project `.env`; configuration that belongs to the
user should live under `<AWESOME_HOME>`.

## Runtime Contract

Extension discovery produces catalog entries. A catalog entry becomes
executable only after it is registered through the shared Tool Registry and
passes Tool Executor policy.

## Catalog, Surface, And Execution

The extension catalog records what was discovered. Product surfaces explain
what users can see. The execution registry defines what the Agent can invoke.
These views must be assembled from the same source so a tool is not displayed
as executable when no handler is registered.

## Related Documents

- [User memory, skills, and MCP](../user-guide/memory-skills-mcp.md)
- [Security model](security-model.md)
