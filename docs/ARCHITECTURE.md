# Agent Team Architecture

This document describes a practical starting architecture for a coding agent project built around multiple specialized roles.

## Goals

- Coordinate coding work through explicit roles.
- Keep task state inspectable and recoverable.
- Make implementation, review, and verification separate enough to improve quality.
- Avoid over-engineering before the first working loop exists.

## Recommended Repository Shape

```text
.
|-- AGENTS.md
|-- DECISIONS.md
|-- PROGRESS.md
|-- README.md
|-- docs/
|   `-- ARCHITECTURE.md
|-- src/
|   |-- agents/
|   |-- orchestrator/
|   |-- tools/
|   |-- memory/
|   `-- runtime/
|-- tests/
`-- examples/
```

Create `src/`, `tests/`, and `examples/` when implementation begins. Until then, keep the repository documentation-first.

## Core Concepts

### Orchestrator

The orchestrator owns the workflow. It receives the user goal, breaks it into tasks, assigns roles, tracks state, and decides when work is complete.

Typical responsibilities:
- task planning
- role assignment
- state transitions
- error handling
- final response assembly

### Agents

Agents are role-specific workers. They should have narrow responsibilities and clear inputs/outputs.

Recommended initial roles:
- `planner`: turns a goal into a concrete task plan.
- `implementer`: edits code and project files.
- `reviewer`: checks for bugs, missing tests, and maintainability risks.
- `tester`: runs verification and reports failures.
- `documenter`: updates progress, decisions, and user-facing docs.

### Tools

Tools are controlled capabilities used by agents, such as file editing, shell commands, browser checks, GitHub actions, or package manager operations.

Keep tool wrappers separate from agent logic so agents can be tested without executing real side effects.

### Memory

Memory should start simple:
- `PROGRESS.md` for human-readable current state.
- `DECISIONS.md` for durable technical choices.
- structured task state later, when the workflow needs restart/resume behavior.

Avoid adding a database before the project has a concrete persistence need.

### Runtime

The runtime executes the agent loop:

1. accept a goal
2. load project context
3. create or update a plan
4. dispatch role-specific work
5. verify outputs
6. update progress and decisions
7. return a concise result

## First Implementation Milestone

Build a minimal local CLI before adding complex integrations:

```text
user goal -> planner -> implementer stub -> reviewer stub -> final report
```

The first version does not need autonomous code editing. It only needs a reliable workflow skeleton with clear logs and state.

## Design Principles

- Make state visible.
- Keep agent roles narrow.
- Treat tool calls as side effects.
- Prefer deterministic tests around planning and state transitions.
- Add autonomy only after the workflow is inspectable.
