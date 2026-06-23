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

Status: accepted

Context:
- Coding agents should discover project instructions and current state without searching deeply.

Decision:
- Keep `AGENTS.md`, `PROGRESS.md`, and `DECISIONS.md` in the repository root.
- Keep deeper design notes under `docs/`.

Consequences:
- Agent context is easy to find.
- Long-form docs can still move into `docs/` as the project grows.

