# Development Agent Execution Plans

These plans coordinate Codex or another agent while modifying this repository.
They are not plans created by the `awesome_agent` product for user tasks.

Local plans live under the ignored directory:

```text
.codex/
`-- exec-plans/
    |-- active/
    `-- completed/
```

Use a local execution plan for work spanning multiple files, systems, or
sessions. Every active plan records:

- objective and confirmed decisions
- scope and explicit non-goals
- milestones and WIP status
- validation commands and evidence
- documentation impact
- risks, blockers, handoff, and next action

Only one milestone may be in progress. Completed plans move from `active/` to
`completed/` locally and remain uncommitted.

Durable conclusions must be extracted from local plans into tracked project
documents such as `ARCHITECTURE.md`, `DECISIONS.md`, product specifications,
design documents, reliability/security rules, or the technical-debt tracker.
