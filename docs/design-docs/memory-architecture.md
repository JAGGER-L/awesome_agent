# Memory Architecture

Both memory layers default to disabled and may be enabled by project
configuration or a single-run override.

## Built-in Memory

- `USER.md`: durable user preferences and communication constraints.
- `MEMORY.md`: durable operational experience and reliable environment facts.

The Leader owns automatic writes when enabled. Other agents submit candidates.
Memory is bounded, deduplicated, attributed, and filtered for secrets.

## Mem0 Platform

Mem0 stores preferences, experience, and summaries, isolated by user and
project. It must not store full source, full conversations, secrets, or raw tool
output. Mem0 failure cannot fail the agent run.

Retrieved memory is untrusted context. It cannot grant capabilities or approve
commands. Retrieved content is fenced to prevent automatic recapture.

