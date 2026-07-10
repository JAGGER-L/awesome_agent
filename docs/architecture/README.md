# Architecture

These documents describe durable system boundaries. They are for contributors
and reviewers who need to understand how product surfaces, runtime authority,
tools, providers, persistence, and recovery fit together.

## Accepted Target

The repository is migrating toward the
[Local-first Coding Agent target architecture](local-first-target.md). That
document is an accepted destination, not a claim that the current source tree
already implements the target.

Key decisions are recorded under [`decisions/`](decisions/). During the
migration, this index deliberately distinguishes current implementation
documents from accepted target documents.

## Current Implementation Reading Order

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

Some current documents describe subsystems that the accepted target will
simplify or remove, including the team runtime, PostgreSQL-backed persistence,
durable workers, generalized approvals, and Docker service modes. They remain
current-state references until the relevant product path is cut over.

## Architecture Boundary

Architecture docs define durable contracts and dependency direction. They do
not track local execution progress, task journals, or raw validation output.
Those belong under `.codex/exec-plans/`, PR notes, or governance docs after the
durable conclusion is known.

For repository source layout, see [ARCHITECTURE.md](../../ARCHITECTURE.md).
For roadmap direction, see [governance roadmap](../governance/roadmap.md).
