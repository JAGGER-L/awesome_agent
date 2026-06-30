# Roadmap Writing Rules

These rules govern future updates to
[`runtime-roadmap.md`](runtime-roadmap.md) and
[`runtime-roadmap-archive.md`](runtime-roadmap-archive.md). They exist to keep
the roadmap durable, readable, and useful for architecture decisions.

## Purpose

The runtime roadmap answers four questions:

1. What product is this project building?
2. What architecture target governs future work?
3. What current phase and numbered tasks are active?
4. What long-term phases are directionally planned but not yet committed as
   numbered tasks?

The roadmap is not a session handoff, implementation checklist, task journal,
chat transcript, or local execution plan.

## File Boundaries

| File | Role | Allowed content |
| --- | --- | --- |
| `runtime-roadmap.md` | Current durable roadmap and architecture direction. | Project positioning, long-term architecture target, invariants, roadmap principles, active phase, carried-forward architecture debt, directional long-term phases, compressed completed milestone summary, and change control. |
| `runtime-roadmap-archive.md` | Historical detail that no longer drives current sequencing. | Old task tables, detailed task breakdowns, historical exit evidence, obsolete gap tables, and superseded sequencing rules. |
| `tech-debt-tracker.md` | Durable debt registry. | Open, resolved, and won't-implement debt with evidence and exit criteria. |
| `.codex/exec-plans/` | Local uncommitted execution notes. | Branch-specific implementation plans, milestone checklists, validation evidence, handoffs, and local blockers. |

Do not move local execution evidence into the roadmap unless it changes durable
product direction, closes a roadmap task, or changes a documented exit
condition.

## Required Roadmap Structure

Keep `runtime-roadmap.md` in this order:

1. `Project Positioning`
2. `Long-Term Architecture Target`
3. `Runtime Governance Invariants`
4. `Roadmap Principles`
5. `Current Kernel Phase` or the current active phase name
6. `Architecture Debt Carried Forward`
7. `Post-Kernel Long-Term Plan` or the next directional long-term plan section
8. `Completed Milestones Summary`
9. `Change Control`

If a section needs a different name, preserve its responsibility. Do not add a
new top-level section when an existing section can carry the information
clearly.

## Writing Rules

- Prefer durable decisions over implementation narration.
- Keep the main roadmap concise enough to read during planning.
- Use archive links instead of copying long historical detail into the main
  roadmap.
- State dependencies and exit conditions explicitly.
- Make every active numbered task testable, observable, or otherwise
  evidence-backed.
- Use directional phases for work that is real but not ready to become a
  numbered task.
- Use precise status words: `Planned`, `Active`, `Done`, `Deferred`,
  `Superseded`, or `Won't implement`.
- Keep the roadmap consistent with `tech-debt-tracker.md`, `ARCHITECTURE.md`,
  and relevant design docs.
- Preserve the token-only runtime budget decision. Do not reintroduce amount,
  price, cost, currency, USD, or billing limits as runtime governance fields.

## What Not To Put In The Main Roadmap

Do not put these in `runtime-roadmap.md`:

- branch names;
- local command transcripts;
- raw test output;
- detailed milestone checklists;
- implementation steps already tracked in `.codex/exec-plans/`;
- long historical task narratives;
- stale gap tables whose gaps are resolved;
- speculative task numbers without entry criteria and exit conditions;
- broad product promises that do not map to a phase, task, debt item, or design
  document;
- chat-only decisions without repository evidence.

Put historical detail in `runtime-roadmap-archive.md`. Put local execution
detail in `.codex/exec-plans/`. Put unresolved debt in
`tech-debt-tracker.md`.

## Numbered Task Rules

A numbered task may be added only when all of these are true:

- It maps to the current active phase or an approved phase transition.
- It has a single architectural purpose.
- It has an exit condition that can be verified.
- It has clear dependency ordering relative to surrounding tasks.
- It does not silently weaken existing invariants.
- It does not duplicate an existing technical debt item without linking to it.

When adding or changing a numbered task, update:

- the active phase table;
- the carried-forward debt table when relevant;
- `tech-debt-tracker.md` when the task opens, closes, narrows, or declares debt
  as a deliberate non-goal;
- the archive only when historical detail is moved out of the main roadmap.

## Status Rules

Use statuses consistently:

| Status | Meaning |
| --- | --- |
| `Planned` | Accepted in the durable roadmap but not started. |
| `Active` | Currently being executed on an implementation branch. |
| `Done` | Exit condition is met with recorded evidence. |
| `Deferred` | Still valid, but intentionally moved behind another dependency. |
| `Superseded` | Replaced by a newer task, phase, or architecture decision. |
| `Won't implement` | Explicit non-goal with rationale and evidence. |

Do not mark a task `Done` because a local plan exists, code was written, or a
branch was opened. `Done` requires evidence from tests, health checks, traces,
durable query APIs, docs, or other committed operational checks.

## Long-Term Phase Rules

Long-term phases are for direction, not commitment. They should describe:

- direction;
- entry criteria;
- expected exit shape.

They should not contain exact task numbers, promised dates, or detailed
implementation steps. Convert a directional phase into numbered tasks only when
the preceding phase has evidence and the next task has a clear exit condition.

## Archive Rules

Move content to `runtime-roadmap-archive.md` when it is useful for
traceability but no longer guides active sequencing. Archive entries should be
shorter than original task handoffs but detailed enough to explain why a past
decision was made.

Do not delete historical roadmap content outright unless it is duplicated,
incorrect, or already preserved in another committed design/governance file.

## Update Procedure

Before editing the roadmap:

1. Inspect `git status`.
2. Read `runtime-roadmap.md`.
3. Read this rules file.
4. Read `tech-debt-tracker.md` when a task opens, closes, narrows, or declares
   debt as a non-goal.
5. Read relevant design docs for architecture-boundary changes.

While editing:

1. Keep the main roadmap focused on durable direction.
2. Move historical detail to the archive.
3. Keep active tasks ordered by dependency risk.
4. Update linked governance files in the same change when their facts change.

After editing:

1. Run `git diff --check`.
2. Search the touched roadmap files for `TODO`, `TBD`, conflict markers, and
   accidental local-plan language.
3. Confirm local `.codex` plans did not become committed roadmap content.
4. Record any unverified assumptions in the final handoff.

## Review Checklist

Before accepting a roadmap update, verify:

- The first screen explains the product positioning and roadmap role.
- Current tasks are few, ordered, and evidence-backed.
- Future work is directional unless it is ready for a numbered task.
- Historical detail is linked or archived instead of bloating the main file.
- Architecture boundaries still match the durable runtime design.
- Token-only runtime governance remains intact.
- The change would help the next agent choose the next task without reopening
  the entire architecture debate.
