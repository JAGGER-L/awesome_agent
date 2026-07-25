# Core Concepts

This section explains the product model behind Awesome. Read it when you want
to predict how the application will behave, not merely memorize commands.

## The Problem the Architecture Solves

A coding agent must combine three things that should never be confused:

1. conversational intent: what the user wants;
2. model reasoning: what action might satisfy that intent;
3. local authority: what the process is actually allowed to do.

If those concerns share one implicit state, cancellation, retries, approvals,
and crash recovery become ambiguous. Awesome therefore gives each concern an
explicit owner and lifetime.

## The Product Model

```text
Workspace
  `-- Session (one running Core, one immutable instruction snapshot)
       `-- Thread (durable conversation and future-Turn choices)
            |-- Turn (one user request and one terminal outcome)
            |    `-- Operation (exclusive live execution lease)
            |         `-- model/tool loop + interactions
            `-- Direct command (Operation without a model Turn)
```

These terms answer different questions:

| Concept | Question it answers | Typical lifetime |
| --- | --- | --- |
| Workspace | Which project and filesystem boundary are active? | Across sessions |
| Session | Which running Core owns this Workspace now? | One process run |
| Thread | Which durable conversation are we continuing? | Across sessions |
| Turn | What happened for one natural-language request? | Durable record |
| Operation | Who owns the mutable foreground right now? | Until success, failure, or cancellation |
| Interaction | What user decision is required to continue safely? | Until resolved or cancelled |
| ChangeSet | Which observed file effects can be reviewed or restored together? | Durable journal record |

## One Request, End to End

```text
submit message
    |
    v
atomically acquire Operation lease
    |
    v
persist Turn + freeze configuration/context facts
    |
    v
prepare bounded context -> call model
                          -> request tool
                          -> validate + policy + approval
                          -> execute + record result/change
                          -> call model again
    |
    v
persist exactly one terminal Turn outcome
```

The lease comes before Turn persistence. This prevents a losing concurrent
request from leaving an empty or permanently in-progress Turn. Tool approval
is a continuation of the same Operation rather than a second operation, which
prevents the approval flow from deadlocking itself.

## Four Independent Safety Layers

Awesome deliberately separates:

- **Workspace trust**, which decides whether project-controlled content may be
  loaded at all;
- **permission mode**, which decides whether a trusted action needs approval;
- **hard safety checks**, which reject selected dangerous paths or commands in
  every permission mode;
- **host isolation**, which Awesome does not currently provide.

This separation avoids a misleading “safe/unsafe” switch. Full access removes
some prompts for known built-in local capabilities, but it does not disable
hard denials, auto-approve MCP, or create a sandbox. See
[Permissions and safety](../user-guide/permissions.md).

## Durable State Versus Session State

Thread messages, Turns, summaries, checkpoints, trust records, extension
enablement, and Change Journal records can survive a restart. The foreground
lease, input queue, active permission mode, temporary write grant, pending UI
state, and the root `AGENTS.md` snapshot belong to the current session.

This distinction is a recovery boundary: Awesome persists facts needed to
explain or safely resume work, but does not pretend that an in-memory authority
grant remains valid after a process or Thread transition.

## Design Tradeoffs

- One foreground mutation owner sacrifices parallel editing inside one session
  for deterministic ordering and recovery.
- An immutable root-instruction snapshot sacrifices hot reload for a stable
  Turn contract.
- Host execution preserves compatibility with normal developer tools but
  requires external isolation for hostile code.
- Conservative recovery may ask the user or refuse restoration instead of
  guessing after an ambiguous external effect.
- Bounded context may summarize older history; the manifest makes that choice
  inspectable.

## Reading Order

1. [Workspace, Thread, Turn, and Operation](workspace-thread-turn.md)
2. [Context and instructions](context-and-instructions.md)
3. [Changes and recovery](changes-and-recovery.md)
4. [Daily workflows](../user-guide/README.md)
5. [Architecture](../architecture/README.md) for implementation ownership

For exact field values and limits, use the [Reference](../reference/README.md).
