# Architecture guide

This guide explains how Awesome preserves one execution authority while a user
request crosses a terminal UI, a private protocol, an application lifecycle,
a LangGraph agent, model providers, tools, and durable state.

The repository-root [architecture overview](../../ARCHITECTURE.md) is the
authoritative topology and ownership map. The pages here answer narrower
questions and point back to source and tests. If a focused page conflicts with
the root overview or current code, the root overview and code win; update the
focused page in the same change.

## Start with the invariant

The central invariant is not “the project uses LangGraph” or “the product has a
TUI.” It is:

> Exactly one Python Application host owns product lifecycle and one Agent
> graph owns model/tool-loop execution. Every surface and extension must cross
> those boundaries instead of creating another execution path.

That invariant makes cancellation, approval, persistence, recovery, and event
ordering reason about the same operation. Most architectural rules follow from
it:

- Ink captures intent and renders typed facts; it does not run models or tools.
- `ApplicationFacade` is the surface-facing product API.
- `application/` admits and coordinates foreground work before durable Turn
  creation.
- `agent/graph.py` is the only `StateGraph` compiler.
- all tools use one Registry -> Policy -> Executor path;
- product records and graph checkpoints have different owners and databases;
- Skills, Memory, workspace instructions, and MCP results are context, not
  authority;
- Full access changes approval behavior but does not create an OS sandbox.

## Choose a reading path

| If you need to understand... | Read |
| --- | --- |
| startup, Turns, direct commands, interactions, cancellation, and shutdown | [Request lifecycles](request-lifecycles.md) |
| why Application and Agent are separate and where LangGraph belongs | [Application and Agent](application-and-agent.md) |
| prompt sources, budgets, model adapters, Skills, Memory, and MCP | [Context, model, and extensions](context-model-and-extensions.md) |
| tool registration, Web search/citations, approval, shell execution, and reversible changes | [Tools and changes](tools-and-changes.md) |
| SQLite ownership, checkpoints, leases, reset, and crash convergence | [Storage and recovery](storage-and-recovery.md) |
| Protocol v4, stdio bounds, Ink state, input modes, and reconciliation | [Protocol and TUI](protocol-and-tui.md) |
| threat model, trust, isolation limits, and enforced dependency direction | [Security and dependencies](security-and-dependencies.md) |

For a code-first tour, begin with:

1. `src/awesome_agent/application/facade.py`
2. `src/awesome_agent/application/composition.py`
3. `src/awesome_agent/application/turns.py`
4. `src/awesome_agent/agent/graph.py`
5. `src/awesome_agent/agent/nodes.py`
6. `src/awesome_agent/context/builder.py`
7. `src/awesome_agent/core/tools/executor.py`
8. `src/awesome_agent/protocol/stdio.py`
9. `tui/src/app/App.tsx`

## Boundaries at a glance

```text
terminal input
    |
    v
Ink surface -- Protocol v4 --> LocalApplication
                                     |
                              foreground arbiter
                                     |
                 +-------------------+-------------------+
                 |                                       |
                 v                                       v
           TurnCoordinator                         command service
                 |
                 v
         compiled Agent graph
                 |
        +--------+--------+
        |                 |
        v                 v
    ModelGateway       ToolExecutor
        |                 |
        v                 v
 provider adapter    built-in / MCP adapter
                          |
                          v
                    workspace / host
```

Arrows show calls, not ownership. Application composes the concrete objects,
but the lower package retains its own invariants. For example, Application may
cancel a Turn; it may not repair provider message chains outside Agent nodes.

## How to read an architecture claim

Each page separates five kinds of statements:

- **responsibility**: work a package is allowed to perform;
- **invariant**: a property that must hold across success, failure, and races;
- **contract**: a typed or persisted shape consumed across a boundary;
- **mechanism**: the current implementation used to enforce the invariant;
- **limit**: behavior the mechanism does not guarantee.

This distinction matters. “File paths are lexically contained” is a mechanism;
“tools must not follow a workspace link to an external target” is an invariant;
“Awesome is an OS sandbox” would be a false guarantee.

## Architecture change checklist

Before changing a boundary:

1. Identify the current owner of the decision and its durable state.
2. Trace all callers, events, recovery paths, and cancellation paths.
3. Decide whether the change alters a public, protocol, storage, or extension
   contract.
4. Add a failing test at the lowest layer that can prove the invariant, then
   add integration coverage for the crossed boundary.
5. Update the root architecture overview if package ownership or dependency
   direction changes.
6. Update the focused page, user documentation, generated Protocol v4 fixtures,
   and TUI schema/presenter when their contracts change.

The contributor workflow is detailed in
[Contracts and documentation](../development/contracts-and-documentation.md).

## Source and test map

| Concern | Primary source | Contract tests |
| --- | --- | --- |
| package and framework ownership | `src/awesome_agent/*` | `tests/structural/test_dependency_architecture.py` |
| Application facade and commands | `application/facade.py`, `dispatcher.py` | `tests/structural/test_application_architecture.py` |
| Agent graph and checkpoint state | `agent/graph.py`, `agent/state.py` | `tests/structural/test_agent_architecture.py` |
| context assembly | `context/`, `application/context.py` | `tests/structural/test_context_architecture.py` |
| tools and Change Journal | `core/tools/`, `core/changes/` | `tests/structural/test_tool_architecture.py` |
| embedded state | `storage/` | `tests/structural/test_storage_architecture.py` |
| extensions | `extensions/`, `memory/` | `tests/structural/test_extension_architecture.py` |
| protocol and TUI | `protocol/`, `tui/src/` | Python and TUI contract/structural suites |
