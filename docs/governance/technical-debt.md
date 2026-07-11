# Technical Debt

Durable project debt belongs here when it remains after a development task ends.
Task-specific notes and handoffs belong in ignored `.codex/exec-plans/`.

| ID | Status | Area | Description | Priority | Exit condition |
| --- | --- | --- | --- | --- | --- |
| TD-035 | Narrowed | Security | Trusted local CLI/TUI execution runs approved commands as the same OS user and is not a security boundary. | High | Documentation, guardrails, redaction, approvals, and optional write bounds remain clear; any stronger boundary is delivered by a sandboxed mode. |
| TD-036 | Open | Sandbox | Docker/AIO sandbox terminology remains mixed with product execution terminology. | Medium | User-facing sandbox configuration is normalized to `local` and `docker`, or AIO is clearly internal. |
| TD-037 | Open | Persistence | Local SQLite initially implements only the embedded product subset, not full Postgres runtime parity. | Medium | Local and Postgres persistence contracts have explicit parity coverage and shared repository contract tests. |
| TD-039 | Open | Persistence | Fresh PostgreSQL migration from an empty database fails because baseline schema and a follow-up migration both add `runs.extension_catalog_version`. | High | `scripts/migrate.ps1` can build the head schema from an empty database. |
| TD-040 | Open | Provider runtime | Conversation model calls use one killable subprocess per model call rather than a persistent warm worker pool. | Medium | Any future pool preserves per-call hard-kill semantics, bounded stderr capture, parent-owned deadlines, and no Run/tool/repository authority in child processes. |
| TD-043 | Open | Documentation depth | Some canonical docs are still thin and need concrete contracts, examples, and failure recovery guidance. | Medium | Structural docs tests reject moved stubs and core user/API/architecture docs contain actionable sections. |

Resolved historical debt is summarized in
[roadmap history](archive/roadmap-history.md) when it helps explain current
direction.
