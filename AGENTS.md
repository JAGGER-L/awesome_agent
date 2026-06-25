# Repository Engineering Agent Contract

These instructions constrain Codex and any other agent modifying this
repository. They do not define the behavior of the `awesome_agent` product at
runtime. Runtime-agent behavior is specified in
`docs/design-docs/runtime-agent-harness.md`.

## Start Work

Before editing:

1. Confirm the repository root and inspect `git status`.
2. Read the active local plan under `.codex/exec-plans/active/`, if one exists.
3. Read the relevant design documents linked from that plan.
4. Inspect recent Git history and existing implementation/tests.
5. Run the repository health and baseline checks once they exist.

If the baseline is unhealthy, fix or record the baseline failure before feature
work.

## Hard Rules

- The repository is the source of truth; do not rely on chat history alone.
- Keep development-agent plans, handoffs, and session state under `.codex/`;
  never commit them.
- Default to `WIP = 1`: finish and verify one plan milestone at a time.
- Commit each completed logical change as its own minimal, reviewable commit.
  A conversation may contain multiple commits; do not wait for the conversation
  to end when an independent change is already complete and verified.
- Keep changes within the active milestone's scope and exclusions.
- Do not weaken acceptance criteria to make work pass.
- Do not mark implementation complete without recorded verification evidence.
- Preserve user changes and never discard unrelated work.
- Keep secrets, credentials, full source, and raw tool output out of memory.
- Use Docker for untrusted execution. Host execution requires explicit
  `--trusted-local` consent.
- Team-mode work requires an independent Verifier before the Leader may finish.
- Update `README.md` and `README.zh-CN.md` together in the same change.
- After changing code, consider whether behavior, interfaces, configuration, or
  architecture described in the docs also changed. Update the relevant docs
  when needed; bug fixes and internal refactors do not require artificial
  documentation edits.

## Validation Order

Run required gates in this order:

1. formatting and lint
2. type checking
3. unit tests
4. structural tests
5. integration tests
6. application startup
7. end-to-end tests for cross-component behavior

Do not advance past a failed lower gate. Record commands, results, and any
unverified paths in the active local execution plan.

## Finish Work

Before ending:

1. Update the active plan status and handoff section.
2. Record validation evidence, blockers, risks, and next action.
3. Update architecture or decision docs when behavior or boundaries changed.
4. Remove temporary files and debugging code.
5. Leave the worktree in a reviewable, recoverable state.

## Documentation Map

- `ARCHITECTURE.md`: system boundaries and dependency direction.
- `docs/design-docs/index.md`: detailed technical designs.
- `.codex/exec-plans/active/`: ignored local scope, evidence, and handoff.
- `docs/engineering/execution-plans.md`: local execution-plan rules.
- `docs/engineering/engineering-harness.md`: rules for repository agents.
- `docs/design-docs/runtime-agent-harness.md`: product runtime harness.
- `docs/QUALITY_SCORE.md`: quality gates and current score.
- `docs/RELIABILITY.md`: failure and recovery requirements.
- `docs/SECURITY.md`: sandbox, approval, and data-safety rules.
