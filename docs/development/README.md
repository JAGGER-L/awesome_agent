# Contributing to Awesome

Awesome is split between a Python Core and an Ink + React TUI. Contributions
are accepted when they preserve product behavior, package ownership, recovery
semantics, and cross-language contracts—not merely when one reproduction
passes.

This section is the contributor path. Product installation and daily use belong
in the getting-started and user guides.

## First contribution path

1. Read the repository-root `AGENTS.md` and
   [architecture overview](../../ARCHITECTURE.md).
2. Follow [Development setup](setup.md) and start the checkout once.
3. Locate the current owner of the behavior in the
   [architecture guide](../architecture/README.md).
4. Add or identify a test that proves the current behavior and the desired
   invariant.
5. Make the smallest coherent change; avoid unrelated refactors.
6. Run the risk-matched gates in [Testing](testing.md).
7. Update contracts and documentation using
   [Contracts and documentation](contracts-and-documentation.md).
8. Inspect the diff and status, then commit one verified logical change.

When adding a provider, built-in tool, command, Skill behavior, MCP behavior,
or protocol feature, read [Extending Awesome](extending-awesome.md) before
editing. Maintainers preparing artifacts use [Release](release.md).

## Repository map for contributors

| Area | Owns | Start with |
| --- | --- | --- |
| `src/awesome_agent/application/` | lifecycle, commands, foreground work, interactions, composition | `facade.py`, `composition.py`, `turns.py` |
| `src/awesome_agent/agent/` | one LangGraph and model/tool loop | `graph.py`, `nodes.py`, `state.py` |
| `src/awesome_agent/context/` | prompt sources, budgets, paths, compression | `builder.py`, `compression.py` |
| `src/awesome_agent/modeling/` | provider-neutral contracts and gateway | `provider.py`, `gateway.py` |
| `src/awesome_agent/providers/` | DeepSeek and Kimi adapters | `deepseek.py`, `kimi.py` |
| `src/awesome_agent/core/tools/` | registry, policy, executor, built-ins, processes | `executor.py`, `registry.py` |
| `src/awesome_agent/core/changes/` | Change Journal and undo/redo | `journal.py`, `operations.py` |
| `src/awesome_agent/storage/` | embedded state and checkpoints | `database.py`, `conversations.py` |
| `src/awesome_agent/extensions/` | Skills and MCP | `skills/`, `mcp/` |
| `src/awesome_agent/memory/` | local and Mem0 memory | `service.py`, `mem0_cloud.py` |
| `src/awesome_agent/protocol/` | Protocol v5 and stdio Host | `jsonrpc.py`, `stdio.py` |
| `tui/src/` | terminal presentation and Core adapter | `app/App.tsx`, `protocol/`, `state/` |
| `tests/` and `tui/tests/` | behavior, integration, structure, packaging | nearest package suite |

## Contribution invariants

- Repository files and tests are the source of truth.
- Preserve unrelated user and agent work in a shared branch or worktree.
- Keep one product authority: Ink presents, Application coordinates, Agent
  reasons, Tool Executor performs effects.
- Do not add compatibility adapters, skips, expected failures, type ignores, or
  weaker assertions solely to make a change pass.
- Expected failure, cancellation, timeout, race, and recovery behavior are part
  of the feature—not optional polish.
- Public behavior, configuration, commands, protocol, storage, and architecture
  changes require documentation in the same change.
- Never commit credentials, private paths, local state, generated caches,
  debug output, or raw tool/provider payloads.

## Change workflow

### 1. Establish scope

State the user-visible goal and the invariant that makes it correct. Trace the
request from the surface through its owner to storage or an external effect.
For a cross-boundary change, write a short execution plan in the ignored active
plan directory.

### 2. Prove the defect or contract

Prefer a deterministic failing test. Include the original case, an equivalent
variant, and a normal negative case. For concurrency or cancellation, use
barriers/events and bounded fake backends instead of sleeps and live services.

### 3. Implement at the owner

Reuse current error types, events, and module boundaries. If a fix appears to
require a second execution path, generic compatibility layer, or new production
dependency, revisit the design before coding.

### 4. Validate progressively

Run format/lint and focused tests first. Stop on a lower-gate failure until it is
fixed or proven unrelated. Add integration, structural, packaging, TUI, and E2E
coverage only where the change crosses those boundaries.

### 5. Hand off evidence

Record exact commands and outcomes, deferred platform/live checks, and residual
risk. Inspect `git diff --check`, the complete diff, and `git status` before a
focused commit or pull request.

## Where to ask a design question

Use ownership to frame the question:

- “Should this state survive restart?” starts with Conversation/Storage.
- “Who may begin this work?” starts with Application foreground admission.
- “What is the next model/tool transition?” starts with Agent.
- “May this effect run?” starts with Tool Policy and Executor.
- “How is this shown?” starts with Protocol facts and the TUI Presenter.
- “Can this be safely resumed?” starts with Turn/checkpoint and external-effect
  evidence.

This framing keeps a framework or component preference from becoming an
architectural decision by accident.

## Pull request expectations

A reviewable pull request explains:

- the user problem and protected invariant;
- the chosen owner and why the boundary is correct;
- behavior and public-contract changes;
- tests added for success, negative, failure, race, cancellation, or recovery
  paths as applicable;
- exact validation performed;
- platform, credential, network, or release checks not performed;
- residual risk and follow-up work kept out of scope.

Do not describe a check as passing when it was skipped, unavailable, or only
inferred from a different environment.
