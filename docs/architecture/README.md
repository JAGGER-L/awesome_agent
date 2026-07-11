# Architecture

These documents describe durable system boundaries for the local product.

## Accepted Target

The repository is migrating toward the
[Local-first Coding Agent target architecture](local-first-target.md). That
document is an accepted destination, not a claim that the current source tree
already implements the target.

The accepted Phase 1 package boundaries, PR units, and headless exit criteria
are specified in the
[Local-first foundation detailed design](local-first-foundation.md).

Key decisions are recorded under [`decisions/`](decisions/). During the
migration, this index deliberately distinguishes current implementation
documents from accepted target documents.

## Current Implementation Reading Order

- [Runtime kernel](runtime-kernel.md)
- [Providers and streaming](providers-streaming.md)
- [Extensions](extensions.md)
- [Memory, skills, and MCP](../user-guide/memory-skills-mcp.md)
- [Security model](security-model.md)

The implemented product path is Application lifecycle -> LangGraph Agent graph
-> ModelGateway -> ToolExecutor -> typed Events and embedded SQLite state.

## Architecture Boundary

Architecture docs define durable contracts and dependency direction. They do
not track local execution progress, task journals, or raw validation output.
Those belong under `.codex/exec-plans/`, PR notes, or governance docs after the
durable conclusion is known.

For repository source layout, see [ARCHITECTURE.md](../../ARCHITECTURE.md).
For roadmap direction, see [governance roadmap](../governance/roadmap.md).
