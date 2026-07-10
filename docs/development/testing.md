# Testing During the Architecture Rewrite

Tests exist to reduce product risk. During the local-first rewrite, the relevant
risk is breaking an accepted target behavior, not failing to preserve an
architecture that is being removed.

## Current Policy

The rewrite uses a target-first test baseline:

- keep tests for portable product invariants and accepted target contracts;
- delete tests that only encode removed runtime, API, worker, PostgreSQL,
  Textual TUI, artifact, approval, team, or deployment behavior;
- do not add compatibility layers, permanent skips, or expected failures only
  to satisfy deleted architecture contracts;
- add tests with each new target module or behavior;
- use Git history, not a quarantine directory, as the archive for removed
  tests.

An old test may be retained only when its observable behavior is still part of
the target product and the test does not force the old architecture to remain.
If the behavior remains but the test is implementation-coupled, record the
behavior in the coverage ledger and rewrite the test with the target module.

## Validation Gates

For each refactor task, run the smallest set that covers its changed boundary:

1. formatting and lint for changed and target-owned Python files;
2. type checking for affected target modules;
3. targeted unit tests;
4. affected structural or contract tests;
5. necessary local integration tests.

The default target baseline is:

```powershell
.\scripts\check.ps1
```

The legacy repository-wide suite, legacy E2E suite, Docker/PostgreSQL system
tests, and legacy smoke tests are not rewrite completion gates. They must not be
used to justify compatibility code for removed behavior.

## Phase Gates

| Phase | Required evidence | Explicitly deferred |
| --- | --- | --- |
| Architecture rewrite | Target lint/type checks, affected unit and contract tests, necessary local integration tests | Legacy full suite, product E2E, external smoke, performance |
| Target feature stabilization | Complete target unit/contract suite, component integrations, startup checks for implemented surfaces | User journeys that do not exist yet |
| Product readiness | Complete target suite, Ink/React TUI E2E, smoke tests, workspace safety flows, upgrade/recovery checks | Nothing required by the release contract |

Coverage percentage is informational until the target module set stabilizes.
It becomes a gate only after the denominator contains target production code
rather than code scheduled for deletion.

## Evidence

Record commands, results, deliberately removed or deferred coverage, unresolved
risks, and the target behavior that will replace deleted coverage in the active
execution plan or final handoff. The current coverage ledger lives in
[`tests/README.md`](../../tests/README.md).
