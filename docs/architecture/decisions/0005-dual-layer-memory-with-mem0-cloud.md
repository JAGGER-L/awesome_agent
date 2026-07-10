# ADR 0005: Dual-layer Memory with Mem0 Cloud

- Status: Accepted
- Date: 2026-07-10
- Scope: Long-term memory

## Context

Conversation history and checkpoints are not substitutes for stable user
preferences and project knowledge. Built-in local memory must remain useful
without a network, while external semantic recall must be optional and have a
clear privacy boundary.

## Decision

Memory has two additive layers:

1. user-scoped `USER.md` and workspace-scoped `MEMORY.md` below user-owned
   `AWESOME_HOME`;
2. optional Mem0 Cloud recall and post-turn writes.

Mem0 Cloud is the only supported external memory service in the first target.
There is no external memory provider registry, provider selector, or routing
system. A thin Mem0 adapter exists only to isolate SDK/network behavior and
enable deterministic tests.

Built-in memory is always available. Mem0 Cloud is opt-in and fail-open. Only
redacted, distilled, stable facts are eligible for external writes. Raw source
code, tool output, secrets, and full conversations are not uploaded by default.

Memory content is untrusted reference context. It cannot change system policy,
workspace trust, or tool permissions.

## Consequences

- Sessions load a bounded built-in snapshot and bounded external recall.
- Built-in writes use explicit add, replace, remove, and list semantics.
- Provider-specific Mem0 tools are not added to the default coding-tool list.
- Additional memory services require a future decision based on a real product
  requirement.

## Rejected Alternatives

- No memory: loses stable preferences and project knowledge between sessions.
- Mem0-only memory: makes core behavior network-dependent.
- A general multi-provider memory framework now: premature abstraction with one
  supported service.
