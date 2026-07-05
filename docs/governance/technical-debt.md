# Technical Debt

Durable project debt belongs here when it remains after a development task ends.
Task-specific notes and handoffs belong in ignored `.codex/exec-plans/`.

| ID | Status | Area | Description | Priority | Exit condition |
| --- | --- | --- | --- | --- | --- |
| TD-003 | Open | Tests | FastAPI TestClient emits an upstream httpx deprecation warning. | Low | FastAPI/Starlette test client no longer emits the warning. |
| TD-031 | Narrowed | Team runtime | Distributed team rework budgets have metrics, but production calibration has not adjusted policy from stable provider/model outcomes. | Medium | Rework budgets are adjusted by reviewed policy based on stable production recovery metrics. |
| TD-035 | Narrowed | Security | Trusted local CLI/TUI execution runs approved commands as the same OS user and is not a security boundary. | High | Documentation, guardrails, redaction, approvals, and optional write bounds remain clear; any stronger boundary is delivered by a sandboxed mode. |
| TD-036 | Open | Sandbox | Docker/AIO sandbox terminology remains mixed with product execution terminology. | Medium | User-facing sandbox configuration is normalized to `local` and `docker`, or AIO is clearly internal. |
| TD-037 | Open | Persistence | Local SQLite initially implements only the embedded product subset, not full Postgres runtime parity. | Medium | Local and Postgres persistence contracts have explicit parity coverage and shared repository contract tests. |
| TD-038 | Open | Runtime architecture | Legacy managed-worktree coding intake remains after chat-first trusted-cwd execution became the product path. | Medium | Managed-worktree coding runtime is removed, isolated as internal, or redesigned as a clear advanced surface. |
| TD-039 | Open | Persistence | Fresh PostgreSQL migration from an empty database fails because baseline schema and a follow-up migration both add `runs.extension_catalog_version`. | High | `scripts/migrate.ps1` can build the head schema from an empty database. |
| TD-040 | Open | Provider runtime | Conversation model calls use one killable subprocess per model call rather than a persistent warm worker pool. | Medium | Any future pool preserves per-call hard-kill semantics, bounded stderr capture, parent-owned deadlines, and no Run/tool/repository authority in child processes. |
| TD-041 | Open | Approval UX | Approval resume, prompt placement, and tool timeline need product-contract coverage across local and Postgres surfaces. | High | Task 109-113 contracts pass and TUI approval UX is stable. |
| TD-042 | Open | Extension registry | Extension catalog display and executable registry can drift when assembled in separate composition roots. | High | A shared runtime tool assembly service is used by local, API, and worker paths. |
| TD-043 | Open | Documentation depth | Some canonical docs are still thin and need concrete contracts, examples, and failure recovery guidance. | Medium | Structural docs tests reject moved stubs and core user/API/architecture docs contain actionable sections. |

Resolved historical debt is summarized in
[roadmap history](archive/roadmap-history.md) when it helps explain current
direction.
