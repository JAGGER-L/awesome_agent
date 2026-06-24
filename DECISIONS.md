# DECISIONS.md

Lightweight decision log for project-level technical choices.

Use this file for decisions that affect architecture, dependencies, agent roles, persistence, runtime behavior, or maintenance. Keep entries short and link to deeper docs when needed.

## Template

```markdown
## YYYY-MM-DD: Decision Title

Status: proposed | accepted | superseded

Context:
- What problem are we solving?

Decision:
- What did we choose?

Consequences:
- What gets easier?
- What tradeoffs or risks did we accept?
```

## 2026-06-23: Keep agent docs at repository root

Status: superseded

Context:
- Coding agents should discover project instructions and current state without searching deeply.

Decision:
- Keep `AGENTS.md`, `PROGRESS.md`, and `DECISIONS.md` in the repository root.
- Keep deeper design notes under `docs/`.

Consequences:
- Agent context is easy to find.
- Long-form docs can still move into `docs/` as the project grows.

Superseded by the 2026-06-24 Harness Engineering decision below.

## 2026-06-24: Use a short root entry and layered repository docs

Status: accepted

Context:
- Large instruction files become stale and reduce agent reliability.
- Active work needs durable scope, evidence, and handoff without empty session
  templates.

Decision:
- Keep `AGENTS.md`, `README.md`, and `ARCHITECTURE.md` at the repository root.
- Store detailed designs and product rules under `docs/`.
- Store active scope, evidence, progress, and handoff in execution plans.
- Remove root `PROGRESS.md` and `session-handoff.md`.

Consequences:
- Agents receive a short entry point and load detailed context on demand.
- Active plans become the human-readable source for progress and recovery.
- Machine-readable plan status supports deterministic WIP control.
