# Architecture

## System Intent

The system is a local-first coding agent runtime. It separates orchestration,
side effects, persistence, observation, and model providers so each can evolve
without coupling the whole application to one vendor.

## Runtime Topology

```text
User
  |
Leader
  |-- Teammate (persistent for the run)
  |     `-- Subagent (isolated, bounded, depth 1)
  |-- Teammate
  `-- Verifier (mandatory in team mode)
```

The Leader is the only agent present initially. It decides between solo and team
mode, owns the task tree, manages Teammates, integrates accepted work, and makes
the final completion decision.

Teammates own durable responsibilities and may communicate through an auditable
mailbox. Each Teammate may independently create up to three Subagents.
Subagents do not participate in team conversation and report only to their
creator.

## Source Layout

```text
src/
`-- awesome_agent/
    |-- agents/
    |-- orchestration/
    |-- domain/
    |-- providers/
    |-- tools/
    |-- sandbox/
    |-- memory/
    |-- persistence/
    |-- observability/
    |-- artifacts/
    |-- api/
    `-- cli/
```

The `src` layout is intentional. `src` is the import root and `awesome_agent` is
the package. Tests must import the installed package rather than accidentally
loading repository files.

## Dependency Direction

```text
api / cli
    -> orchestration
        -> domain

providers / tools / sandbox / memory / persistence / observability / artifacts
    implement ports owned by domain or orchestration
```

Rules:

- `domain` does not import infrastructure or framework modules.
- `orchestration` owns workflows but not concrete storage or provider details.
- provider-specific message types do not cross provider boundaries.
- every Agent records its resolved model for API and event traceability.
- tool execution always passes through approval and sandbox policies.
- runtime events are immutable and all state changes emit an event.
- implementation agents cannot approve their own team-mode work.

These rules must be enforced with structural tests once source modules exist.

## Persistence

PostgreSQL is authoritative for LangGraph checkpoints and project-owned runtime
records. Checkpoint semantics remain owned by LangGraph. The API reads runs,
agents, tasks, and event history through a runtime repository instead of
process-local dictionaries. The in-memory repository is an explicit test
adapter only. `EventStream` carries live SSE delivery but is not durable state.

Project tables store runs, agents, tasks, messages, tool calls, artifacts,
approvals, verification, and memory audit data. Agent records include the
resolved model assignment.

Large outputs live in external artifact storage. PostgreSQL stores metadata,
hashes, ownership, and paths.

## Security Boundary

Docker is the default command execution boundary. CLI users may explicitly opt
into trusted local execution. FastAPI runs cannot use trusted-local mode.
Writing Teammates use isolated Git worktrees.

## Detailed Designs

See [docs/design-docs/index.md](docs/design-docs/index.md).
