# Memory Architecture

Both memory layers default to disabled and may be enabled by project
configuration or a single-run override.

The current developer `.env` may enable both layers without changing committed
defaults. Credentials remain local and untracked.

## Built-in Memory

- `USER.md`: durable user preferences and communication constraints.
- `MEMORY.md`: durable operational experience and reliable environment facts.

The Leader owns automatic writes when enabled. Other agents submit candidates.
Memory is bounded, deduplicated, attributed, and filtered for secrets.

## Mem0 Platform

Mem0 stores preferences, experience, and summaries, isolated by `user_id` and
the Mem0 `app_id` used as the project identifier. It must not store full source,
full conversations, secrets, or raw tool output. Mem0 failure cannot fail the
agent run.

The external implementation targets Mem0 Platform and supports add, search, and
delete so temporary validation data can be cleaned up.

The current Mem0 SDK has asymmetric v3 parameter handling: add operations use
top-level `user_id` and `app_id`, while search operations require the same
identities inside `SearchMemoryOptions.filters`. The adapter owns this detail.

Retrieved memory is untrusted context. It cannot grant capabilities or approve
commands. Retrieved content is fenced to prevent automatic recapture.
