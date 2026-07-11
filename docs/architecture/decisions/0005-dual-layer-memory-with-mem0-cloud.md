# ADR 0005: Dual-layer memory with Mem0 Cloud

- Status: Accepted and implemented
- Date: 2026-07-10

## Decision

Memory has two independent, default-off layers:

1. user `USER.md` and workspace-scoped `MEMORY.md` under user-owned
   `AWESOME_HOME`;
2. optional Mem0 Cloud recall and distilled post-Turn writes.

Mem0 Cloud is the only external memory Provider in V1. There is no provider
registry or routing abstraction. Only redacted stable facts are eligible for
cloud writes; raw source, tool output, secrets, and full conversations are not
uploaded by default. Failures produce diagnostics and do not stop local work.

Memory is untrusted context. It cannot alter system policy, trust, or tool
permissions.

## Consequences

Users can enable either layer without enabling the other. Local mutations have
explicit add/replace/remove/list semantics. Additional cloud providers require
a real second use case and a new decision.

## Rejected

No memory loses stable preferences. Cloud-only memory makes Core
network-dependent. A general provider framework is premature with one service.
