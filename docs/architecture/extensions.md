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

Extension discovery produces catalog entries. Catalog entries become executable
only when runtime capability policy grants them to a route, role, or assignment.

## Related Documents

- [User memory, skills, and MCP](../user-guide/memory-skills-mcp.md)
- [Tool capabilities](tool-capabilities.md)
- [Security model](security-model.md)
