# Execution Plans

Local execution plans coordinate development agents while changing this
repository. They are not product roadmaps and are not created by the Awesome
Agent runtime for user tasks.

Plans live under ignored `.codex/exec-plans/`:

```text
.codex/
`-- exec-plans/
    |-- active/
    |-- pending/
    `-- completed/
```

Use `active/` for the current task, `pending/` for approved future work, and
`completed/` for closed local plans. Durable decisions must be extracted into
tracked documentation such as [architecture](../architecture/README.md),
[governance](../governance/README.md), or [operations](../operations/README.md).

The legacy plan guide remains at
[engineering execution plans](../engineering/execution-plans.md) until all
inbound links move.
