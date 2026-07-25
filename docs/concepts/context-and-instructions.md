# Context and Instructions

This page explains what the model sees, why the transcript is not simply sent
verbatim forever, and how project instructions differ from Memory and Skills.
Use `/context` whenever you need evidence for a particular Turn.

## Context Is a Built Artifact

The model receives a bounded sequence assembled by Core. It is not allowed to
read the terminal, database, or Workspace implicitly.

```text
product identity/instructions ----+
root AGENTS.md snapshot -----------+
selected Skill --------------------+
Memory ----------------------------+--> order, deduplicate, budget --> model
Thread summary + recent history ---+
direct-command history ------------+
explicit @paths -------------------+
current request + open tool chain -+
```

Every retained source becomes a context-manifest item with its kind, source ID,
order, estimated Tokens, content hash, and truncation state. `/context` shows
the latest meaningful manifest and budget without exposing credentials.

## Authority Is Not Determined by File Format

Several inputs are Markdown, but they do not have equal authority:

- product instructions are mandatory system policy;
- an accepted root `AGENTS.md` is mandatory Workspace instruction;
- an active Skill supplies mandatory workflow instructions for that Turn;
- Memory is explicitly wrapped as untrusted reference data;
- conversation history, direct-command outcomes, and explicit paths provide
  evidence and task context;
- the current user request states the immediate goal.

Memory cannot override tool policy merely by containing imperative prose.
Likewise, a Skill can guide which tools to use but cannot bypass the registry,
permission decision, path policy, or command circuit breaker.

## Root `AGENTS.md` Is a Session Snapshot

After trust, Core looks for exactly one plain `AGENTS.md` at the Workspace root.
It performs a bounded, identity-checked read and freezes the result for the
Session. The file is accepted only when it is stable UTF-8 text without NUL
bytes, links, junctions, reparse components, or a path escape.

The byte ceiling is 32 KiB. Its token allocation is the smaller of 8,192 Tokens
and 10% of the effective input budget. Exceeding either limit rejects the whole
file. Awesome does not keep an arbitrary prefix because truncating “do X only
when Y” can reverse the meaning of project policy.

Accepted Workspace instructions remain a distinct manifest source even if the
same words appear in product or Skill content. Missing instructions are normal.
A rejected file produces a structured warning in Welcome, the status line, and
`/doctor`; fix the file and start a new Session to take a new snapshot.

Example root instructions:

```markdown
# Project instructions

- Run the focused package test before the full suite.
- Do not edit generated files under `dist/`.
- Keep public APIs backward compatible unless the task explicitly changes one.
```

Keep instructions stable, repository-specific, and verifiable. Put temporary
task details in the request rather than repeatedly editing `AGENTS.md`.

## Explicit Paths

An `@path` reference asks Core to add a specific Workspace file or directory
reference to the current request. It is useful when a large repository has
several plausible entry points:

```text
Review @src/payments/settlement.py and its tests for retry safety.
```

Path references use the same bounded, no-follow Workspace reads as built-in
tools. They do not grant access outside the Workspace and do not make a path
editable. Prefer a narrow file or directory over `@.` so the relevant evidence
fits without displacing other context.

## Budgeting and Ordering

Core first takes the smaller of the configured context total and the selected
model's actual context limit. It reserves output capacity and a safety margin;
the remainder is the effective input limit. Mandatory sources must fit in that
limit or the Turn fails instead of silently dropping policy.

Optional sources are ordered deterministically and consume the remaining
budget. Duplicate optional content is removed within its trust class; timeline
entries remain ordered even when text repeats. Long-term Memory has its own
bounded share, so an accumulated memory store cannot crowd out the current
request.

Exact defaults and limits are in the
[configuration reference](../reference/configuration.md).

## Compression

When context approaches its threshold, Awesome can summarize older completed
Turns while retaining recent Turns in full. The summarization prompt asks the
model to retain goals, decisions, files changed, validation, failures,
direct-command outcomes, and unresolved work. The result is stored with the
sequence it covers and is itself shown in the manifest; exact wording and
details from the summarized Turns can still be lost.

```text
older completed Turns --model summary--> durable Thread summary
recent four completed Turns ---------------------------> retained verbatim
current/open work -------------------------------------> retained verbatim
```

Run `/compact` to request compression explicitly. A no-op is possible when
there is not enough completed history to summarize. Automatic compression also
consumes configured model/retry/compression budgets. If compression fails but
the current context still fits, the Turn can continue with the existing
context; if mandatory content itself exceeds the limit, it cannot.

Compression trades exact old wording for continued coherence. Start a new
Thread when exact historical phrasing is more important than carrying a long
conversation indefinitely.

## Inspect and Diagnose

Use:

```text
/context
/usage
/compact
/doctor
```

`/context` answers what was assembled; `/usage` answers what the Thread has
consumed; `/compact` changes the Thread summary; `/doctor` explains a rejected
Workspace instruction file. If the model appears to ignore a file, confirm
that it is present in the context manifest or reference it explicitly with
`@path`.

For extension-specific context, read [Memory](../extensions/memory.md) and
[Skills](../extensions/skills.md). For implementation ownership, see the
[architecture guide](../architecture/README.md).
