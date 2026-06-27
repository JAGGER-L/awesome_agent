# Task and Verification Model

The Leader owns a dynamic task tree. Every task has one primary owner, optional
collaborators, acceptance criteria, dependencies, blockers, evidence, and full
revision history.

States:

```text
TODO -> READY -> IN_PROGRESS -> SUBMITTED -> VERIFYING
                                  |              |
                                  |              +-> VERIFIED -> DONE
                                  +-> BLOCKED     `-> REJECTED -> IN_PROGRESS

Any active state may move to CANCELLED through an authorized decision.
```

Teammates may update their assignments and create child tasks beneath them.
Subagents cannot edit the task tree.

Team-mode work must be reviewed by the Verifier. The Verifier may run tools and
create verification Subagents but may not repair implementation. Failed work
returns directly to the responsible Teammate. Only the Leader converts
`VERIFIED` to `DONE`.

Verifier failure is classified before choosing rework or failure:

- verification rejection caused by model output quality returns the Todo to the
  responsible Teammate for bounded rework;
- verifier execution or external failures use a smaller retry budget and then
  fail the Run if the review cannot be completed safely.

Task 13 defaults to 10 verification-rejection reworks and one verifier
execution retry for `team-coding@1`. These are conservative defaults, not a
final product policy; tuning by task type, model quality, cost, and risk is
tracked as technical debt.

Every task revision is persisted and exported as a `plan.json` artifact.
