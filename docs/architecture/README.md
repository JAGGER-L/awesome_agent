# Architecture guide

The root [architecture map](../../ARCHITECTURE.md) is the short overview.
These documents define the retained V1 contracts:

- [Agent Core](agent-core.md)
- [Application and LangGraph](runtime-and-langgraph.md)
- [stdio protocol and Ink](protocol-and-ink.md)
- [Persistence](persistence.md)
- [Security](security.md)

Accepted decisions:

1. [Python, LangGraph, and a thin runtime](decisions/0001-python-langgraph-thin-runtime.md)
2. [SQLite and disposable development state](decisions/0002-sqlite-and-disposable-development-state.md)
3. [Workspace trust and execution policy](decisions/0003-workspace-trust-and-execution-policy.md)
4. [Tool kernel, Change Journal, and commands](decisions/0004-tool-kernel-change-journal-and-commands.md)
5. [Dual-layer memory with Mem0 Cloud](decisions/0005-dual-layer-memory-with-mem0-cloud.md)
6. [Python Core and Ink stdio boundary](decisions/0006-python-core-and-ink-stdio-boundary.md)

Architecture documents describe current code contracts. Execution progress and
validation logs belong in ignored `.codex/exec-plans/` or PR evidence.
