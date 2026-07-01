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
- Keep `.codex/exec-plans/active/` small. It should contain only the current
  execution plan and, when explicitly requested, the next approved plan. After
  a task is verified, merged, or otherwise closed, move its local plan to
  `.codex/exec-plans/completed/`. Use `.codex/exec-plans/pending/` for future
  plans that are accepted but not yet active.
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

- `README.md` / `README.zh-CN.md`: user-facing project introduction,
  quickstart, feature overview, and docs entry points.
- `docs/README.md`: reader-oriented documentation index.
- `ARCHITECTURE.md`: system boundaries, source layout, and dependency
  direction.
- `docs/getting-started/quickstart.md`: manual first-run path.
- `docs/user-guide/README.md`: user-facing runtime surfaces.
- `docs/operations/README.md`: local operation, readiness, diagnostics, and
  workspace guidance.
- `docs/design-docs/index.md`: durable architecture design contracts.
- `docs/project-governance/documentation-governance.md`: where project
  information belongs.
- `docs/project-governance/runtime-roadmap.md`: durable runtime roadmap.
- `docs/project-governance/tech-debt-tracker.md`: durable debt registry.
- `docs/engineering/execution-plans.md`: local execution-plan rules.
- `docs/engineering/engineering-harness.md`: rules for repository agents.
- `docs/design-docs/runtime-agent-harness.md`: product runtime harness.
- `docs/QUALITY_SCORE.md`: quality gates and current score.
- `docs/RELIABILITY.md`: failure and recovery requirements.
- `docs/SECURITY.md`: sandbox, approval, and data-safety rules.
- `.codex/exec-plans/active/`: ignored local current execution plans.
- `.codex/exec-plans/completed/`: ignored local completed execution plans.
- `.codex/exec-plans/pending/`: ignored local future accepted plans.

## Repository Map

- `src/awesome_agent/domain/`: framework-free domain models, enums, and
  transition rules.
- `src/awesome_agent/runtime/`: durable graph routes, AgentLoop integration,
  dispatch, worker execution, context, budgets, validation, and team runtime.
- `src/awesome_agent/runtime/agent_loop/`: model/tool loop contracts and
  middleware.
- `src/awesome_agent/extensions/`: extension catalog, skill, MCP, community
  tool, diagnostics, and project extension config adapters.
- `src/awesome_agent/tools/`: built-in tool specs, registry, executor,
  approval policy, repository tools, shell, and artifact tool.
- `src/awesome_agent/modeling/` and `src/awesome_agent/providers/`:
  provider-neutral model protocol and concrete provider adapters.
- `src/awesome_agent/persistence/`: PostgreSQL adapters.
- `src/awesome_agent/api/` and `src/awesome_agent/cli/`: user and operator
  surfaces.
- `scripts/`: local developer and operations helper scripts.
- `skills/`: project runtime skill packages discovered as extension inventory.
- `tests/`: unit, integration, e2e, and structural tests.
