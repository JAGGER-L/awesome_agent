# Documentation Governance

## Purpose

This document defines where project information belongs. It keeps public
product docs, operator guidance, durable architecture contracts, local
execution plans, and roadmap governance from drifting into each other.

## Reader Classes

| Reader | Needs | Entry point |
| --- | --- | --- |
| New user | Understand the project and run it once | `README.md`, `docs/getting-started/quickstart.md` |
| Product user | Use the local CLI and product controls | `docs/user-guide/README.md` |
| Operator | Start, inspect, and diagnose local runtime modes | `docs/operations/README.md` |
| API integrator | Understand resource contracts | `docs/api/README.md` |
| Contributor | Modify this repository safely | `docs/development/README.md` |
| Architecture reviewer | Understand durable runtime boundaries | `docs/architecture/README.md` |
| Maintainer | Govern roadmap, debt, quality, and documentation | `docs/governance/README.md` |
| Reference reader | Inspect generated or compact reference material | `docs/reference/README.md` |

## File Boundaries

| Location | Owns | Must not contain |
| --- | --- | --- |
| `README.md` / `README.zh-CN.md` | Product intro, shortest setup path, docs links, safety note | Internal task history, implementation evidence, architecture deep dives |
| `docs/getting-started/` | First-run setup and verification | Full diagnostics matrix or runtime design |
| `docs/user-guide/` | User-visible product behavior | API resource specs, roadmap governance, implementation internals |
| `docs/operations/` | Startup modes, readiness, logs, runtime data, troubleshooting | Product marketing copy or durable architecture contracts |
| `docs/api/` | API resource shapes and integration contracts | TUI usage walkthroughs |
| `docs/development/` | Rules for humans and coding agents modifying this repository | Runtime agent behavior as a product feature |
| `docs/architecture/` | Durable system boundaries and design contracts | Local session handoffs or task journals |
| `docs/governance/` | Roadmap, technical debt, quality, documentation policy | Per-branch execution plans or raw command output |
| `docs/reference/` | Generated and compact references | Hand-authored decisions |

## Local Execution State

Development-agent plans, handoff notes, validation transcripts, and branch-local
blockers belong under ignored `.codex/exec-plans/`. They may inform tracked
documentation only after their durable decisions have been verified and
extracted into the appropriate reader-owned document.

## README Rules

README files must stay short enough for a new reader to decide whether to try
the project and complete the first local setup path. English and Chinese
READMEs must be updated together in the same change.

README files may summarize implemented user-facing capabilities. Detailed
architecture belongs in `docs/architecture/`; sequencing and priorities belong
in `docs/governance/roadmap.md`.

## Roadmap Rules

`docs/governance/roadmap.md` is a product direction document. It should describe
current state, strategic pillars, now/next/later priorities, active
initiatives, explicit non-goals, dependencies, completed milestones, and change
policy.

The roadmap must not become a task journal, local execution plan, raw test log,
chat transcript, or long historical task table. Historical detail belongs in
`docs/governance/archive/roadmap-history.md`. Open durable gaps belong in
`docs/governance/technical-debt.md`.

## Redirect Policy

Tracked docs should not preserve moved-only redirect shells. When a document is
moved during a docs reorganization, update inbound links in the same change and
delete the old file unless an external compatibility requirement is explicitly
documented.

## Update Procedure

Before changing docs, identify the reader, canonical file, and links that must
be updated. After changing docs, run markdown link and affected structural
tests. If behavior, configuration, startup, security, or runtime boundaries
changed, update the relevant user, operations, API, or architecture document in
the same branch.
