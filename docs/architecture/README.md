# Architecture

These documents describe durable system boundaries. They are for contributors
and reviewers who need to understand how product surfaces, runtime authority,
tools, providers, persistence, and recovery fit together.

## Reading Order

- [Runtime kernel](runtime-kernel.md)
- [Agent loop](agent-loop.md)
- [Team runtime](team-runtime.md)
- [Product surfaces](product-surfaces.md)
- [Tool capabilities](tool-capabilities.md)
- [Persistence and recovery](persistence-recovery.md)
- [Providers and streaming](providers-streaming.md)
- [Extensions](extensions.md)
- [Memory, skills, and MCP](../user-guide/memory-skills-mcp.md)
- [Observability](observability.md)
- [Security model](security-model.md)

## Architecture Boundary

Architecture docs define durable contracts and dependency direction. They do
not track local execution progress, task journals, or raw validation output.
Those belong under `.codex/exec-plans/`, PR notes, or governance docs after the
durable conclusion is known.

For repository source layout, see [ARCHITECTURE.md](../../ARCHITECTURE.md).
For roadmap direction, see [governance roadmap](../governance/roadmap.md).
