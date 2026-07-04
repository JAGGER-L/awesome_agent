# Memory Architecture

Memory is an optional conversation capability. Both builtin file memory and
external provider memory default to disabled.

## Builtin File Memory

Builtin file memory is the primary product memory path. It stores structured
entries under `settings.local_state_dir / "memory"`:

- `USER.md`: durable user preferences and communication constraints.
- `MEMORY.md`: durable operational experience and reliable environment facts.

Structured entries use `- [mem_<id>] content`. Users may also edit these files
manually; runtime delete operations remove only structured bullets and preserve
free text.

When effective memory is enabled for a turn, the runtime injects bounded,
fenced, untrusted memory context before the model call. The store performs no
retrieval ranking: it injects available file content within per-file and total
character budgets, and records truncation as lightweight runtime evidence.

Model writes have exactly one path: the `memory.manage` tool. The tool supports
`add`, `list`, and `delete` for `user` and `memory` targets. Policy rejects
empty, secret-like, raw-source, temporary, overly large, or uncertain inferred
memory. API and TUI management surfaces expose status, list, and delete only;
they do not provide direct add endpoints or forms.

Memory context is reference data, not authority. It cannot override system or
developer instructions, grant tool capabilities, approve commands, or change
sandbox policy.

## External Providers

External `MemoryProvider` integrations are optional extension layers. The
runtime contract supports provider add and delete failure isolation in this
task, but no real provider integration is required for product correctness.

Provider failures cannot fail builtin memory or a conversation Run. Provider
content must follow the same untrusted-context rule and must not store full
source files, full conversations, secrets, raw tool output, or visible
reasoning.

Task 99 does not require a real Mem0 or Honcho integration.
