# Teammate Local Validation

## Purpose

Task 25 adds deterministic validation for distributed writing Teammate child
Runs. The goal is to prevent a writing Teammate from publishing a completed
child result and patch artifact until its child workspace has passed the same
class of project validation gates used by solo modifying Runs.

This design closes the current distributed-team hardening gap without moving
durable coordination back into the role loop. The first implementation is a
publish-before-complete gate, not same-child local model rework.

## Selected Approach

Use a Teammate-local publish gate for writing assignments:

1. `TeamRoleGraph` runs the assignment-scoped `RoleLoop`.
2. If the assignment is write-capable and the role loop produced a patch,
   deterministic validation runs against the Teammate child workspace.
3. Validation execution is wrapped by `TeamAgentLoop.run_agent_operation` with
   structural metadata such as `team_operation=role_validation`.
4. If validation passes, `TeamRoleGraph` persists the patch artifact and records
   a `TeamChildResult(status="completed")`.
5. If validation fails, `TeamRoleGraph` records a
   `TeamChildResult(status="failed", failure_kind="validation_failed")` and does
   not publish the patch artifact as a completed result.

Existing Leader and Verifier semantics then handle the failed child result. The
Verifier cannot pass when sibling Teammate results are not completed, and the
Leader's replacement rework path can create a new Teammate attempt with the
validation summary as durable evidence.

## Non-Goals

- Do not add same-child bounded model rework in Task 25. That behavior remains a
  future enhancement after the distributed team kernel is stable.
- Do not change Subagents into writing agents. Subagents remain read-only and do
  not run local patch validation.
- Do not add a new validation storage model. Reuse
  `ValidationRepository`, `DurableValidationReport`, and
  `DurableValidationGateResult`.
- Do not move patch aggregation, child waits, assignment lifecycle, or terminal
  graph state transitions into middleware.

## Component Boundaries

`TeamRoleGraph` remains responsible for durable state and graph control flow:

- assignment loading and assignment graph validation;
- team budget checks;
- active Subagent waits and Teammate-owned Subagent creation waits;
- deciding whether a writing Teammate result needs validation before publish;
- persisting completed or failed `TeamChildResult` records;
- persisting patch artifacts only for validation-passed completed results;
- mapping the graph return state.

`TeamAgentLoop` remains the middleware stage boundary:

- validation execution is treated as a team agent operation;
- observability middleware can record an `agent.run` span for validation;
- middleware metadata stays structural and excludes patch bodies, raw command
  output, prompts, and tool results.

`TeamRoleValidationMiddleware` owns validation policy:

- resolving validation plans from `.agents/validation.toml` or project
  detection;
- running validation gates through `execute_validation_plan`;
- storing validation reports through the configured `ValidationRepository`;
- converting pass or fail reports into a bounded validation outcome for
  `TeamRoleGraph`;
- emitting `VERIFICATION_CREATED` events with report id, status, attempt, and
  summary.

## Validation Plan Source

Distributed writing Teammate validation uses the same resolver behavior as
solo modifying validation:

```text
load_validation_config(workspace) or detect_validation_plan(workspace)
```

Missing gates are a hard failure for write-capable Teammates that produced a
patch. This matches the solo modifying completion invariant and avoids
publishing unvalidated patches as successful team output.

Read-only Teammates, Subagents, and writing Teammates that produce no patch do
not run this gate in Task 25. A no-patch writing Teammate can still complete
with no patch artifact, preserving the existing role-loop behavior for
assignments that determine no change is needed.

## Failure Semantics

Validation pass:

- durable validation report status is `passed`;
- a `VERIFICATION_CREATED` runtime event is emitted;
- patch artifact is persisted;
- child result status is `completed`;
- graph phase remains `completed`.

Validation fail:

- durable validation report status is `failed`;
- a `VERIFICATION_CREATED` runtime event is emitted;
- patch artifact is not published as a completed child result;
- child result status is `failed`;
- child result `failure_kind` is `validation_failed`;
- child result summary includes the validation summary and remains compactable
  through the existing child-result compaction helper.

The child Run can still fail at the Worker level with a permanent execution
error after recording the failed child result. The durable result is what lets
the parent Leader resume and route replacement rework.

## Observability

Validation must not remain a Worker-side projection. It enters the
`TeamAgentLoop` as a first-class team operation so existing AgentLoop
middleware can observe it. The operation metadata should include only bounded
structural values:

- `team_operation`: `role_validation`
- `assignment_id`
- `team_role`
- `agent_kind`
- `runtime_route`
- `team_root_run_id`

Validation command arguments and command output belong in durable validation
gate records, not in AgentLoop metadata.

## Testing Strategy

Task 25 should be implemented with test-first changes:

- a writing Teammate that produces a patch and passes validation records a
  validation report, emits verification evidence, records a completed child
  result, and publishes a patch artifact;
- a writing Teammate that produces a patch and fails validation records a failed
  validation report, records a failed child result with
  `failure_kind="validation_failed"`, and does not publish a patch artifact;
- validation execution goes through `TeamAgentLoop.run_agent_operation`, proving
  middleware and observability can wrap it;
- read-only roles and no-patch writing roles skip validation;
- validation command output does not enter AgentLoop metadata.

Focused unit tests should live in `tests/unit/test_team_role_graph.py` and
`tests/unit/test_team_agent_loop_middleware.py`. Existing validation executor
and persistence tests remain the source of truth for command execution and
report storage behavior.

## Risks And Follow-Ups

The main trade-off is that failed validation does not feed back into the same
Teammate model loop. This keeps Task 25 bounded and aligns with the distributed
team's existing replacement rework model, but it may use an additional child Run
where solo modifying would repair inside one Run.

Future work may add same-child bounded local rework after the runtime kernel is
stable. That enhancement should explicitly reconcile same-child validation
feedback with Leader-created replacement attempts so the team does not perform
duplicate repair loops.
