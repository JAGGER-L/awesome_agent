# Runtime Agent Configuration

This directory is reserved for configuration, templates, and policy loaded by
the `awesome_agent` product when it runs as a coding agent.

It is not a storage location for Codex or another repository-maintenance
agent's execution plans, handoff notes, or session state. Development-agent
state belongs in the ignored `.codex/` directory.

Runtime-generated state belongs in PostgreSQL or the ignored
`.awesome-agent/` directory, not here.
