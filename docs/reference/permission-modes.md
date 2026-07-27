# Permission mode reference

Permission modes answer one narrow question: when a validated tool requests a
capability, may Core proceed automatically or must it ask the user? They do not
replace workspace trust, path validation, the shell circuit breaker, Change
Journal capture, timeouts, or OS isolation.

## Exact matrix

| Mode | Workspace read | Create/modify | Delete | Shell | Network read | MCP/unknown extension |
| --- | --- | --- | --- | --- | --- | --- |
| Request approval | Allow | Ask | Ask | Ask | Ask | Ask |
| Accept edits | Allow | Allow | Ask | Ask | Ask | Ask |
| Full access | Allow | Allow | Allow | Allow | Ask | Ask |

The matrix applies to Agent tool calls in the selected Thread permission
session. A direct `! command` is explicit authority for that exact command and
uses an independent Direct Full-access session, so it does not show the ordinary
shell prompt; all schema, hard-deny, runner, audit, timeout, and cancellation
controls remain. Local Memory is the other policy exception described below.

The corresponding mode values are `request_approval`, `accept_edits`, and
`full_access`. `/permissions` opens the mode picker;
`/permissions <value>` requests one directly.

Read permission assumes the workspace has already passed the startup trust
boundary. Workspace trust is about whether repository-controlled configuration,
instructions, and extensions may be loaded at all; permission mode is about an
individual capability after activation.

## Why there are three modes

**Request approval** maximizes visibility. Every built-in mutation and shell
execution pauses for a decision. It is the session default and the mode restored
when switching Threads.

**Accept edits** removes repetitive prompts for ordinary creation and exact file
modification, the most common coding-agent workload. It deliberately keeps
deletion and shell separate: overwriting a known file is reversible through the
Change Journal, while recursive deletion or an arbitrary process has a broader
failure surface.

**Full access** removes per-call prompts for built-in local capabilities. It is
useful for a trusted repository and a supervised long task, but it is not “allow
everything.” The first `network.read` call still asks, while MCP and future
unknown extension capabilities ask once per call because Core cannot infer
their external authority or idempotency.

This asymmetric design follows least authority: convenience is granted only
where Awesome knows the capability semantics.

## Evaluation order

Tool safety is split across admission and the concrete handler. Before an
approval can be requested, Core performs:

1. registry lookup and registered Pydantic/schema validation;
2. lexical path-syntax checks for the built-in path tools;
3. the shell circuit breaker against the requested lexical working directory;
4. capability policy: a valid temporary grant, then the current mode matrix;
5. one interaction bound to the Tool call when the result is `ask`.

`network.read` is an explicit matrix exception: without a current Thread grant,
it returns `ask` in all three modes. A permission mode cannot silently authorize
transmission of a search query to Tavily.

After admission, but before the effect, the handler applies backend-specific
checks. Filesystem handlers resolve containment, link/reparse state, object
identity, sensitive names, and protected delete targets. `execute` resolves its
actual working directory and runs the same command policy again before spawn.
Memory mutations check workspace/Agent/Turn authority, content policy, and
compare-and-swap state. MCP arguments have already been schema-validated and a
generation check still precedes remote I/O.

Approval changes none of those handler checks. Full access can bypass a prompt;
it cannot make a malformed request, protected filesystem operation, stale
generation, or circuit-breaker denial valid.

```text
Capability request
       |
       v
  lexical hard deny? - yes --------> DENY
       |
       no
       v
known built-in? ----- no ---------> ASK ONCE
       |
       yes
       v
valid temporary grant? -- yes ----> ALLOW
       |
       no
       v
apply mode matrix ----------------> ALLOW or ASK ONCE
       |
       v
handler safety checks ------------> EFFECT or DENY
```

## Approval choices and temporary grants

A normal approval is bound to the pending Tool call's Thread, Turn, operation,
and interaction generation. The choices are:

- **Yes** (`allow_once`): authorize only this call;
- **Yes, allow all edits during this session** (`allow_thread_writes`): shown
  only for `workspace.write`; authorize later creates/modifications on the
  current selected Thread;
- **No** (`deny`): reject the call.

For `network.read`, the safe first/default choice is **Deny** (`deny`), followed
by **Allow once** (`allow_once`) and **Allow for this Thread**
(`allow_thread_network`). The Thread choice covers later Web searches only; it
does not grant another network capability.

The “all edits” label does not include delete, shell, MCP, or an unknown
capability. Core rejects any attempt to apply that decision to a non-write
capability as an invariant violation.

Temporary capability grants are in-memory session state. Every mode change,
including changing from one mode back to the same conceptual authority through
a new transition, clears the grant set and increments the permission
generation. Selecting, creating, or resuming another Thread resets the mode to
Request approval and clears grants. Nothing is persisted as a permanent rule.

The network grant is also cleared by a Runtime rebuild, `/web revoke`, `/web
off`, or shutdown. Therefore enabling Web and granting network access are
separate decisions.

## Web network reads

An enabled `web_search` sends its query to Tavily. The approval describes that
transfer, and the data is processed under the [Tavily Privacy
Policy](https://www.tavily.com/privacy) and [Tavily Platform
Terms](https://www.tavily.com/terms). Search results remain untrusted model
context; approval does not make their content authoritative.

In headless mode, `--allow-network` resolves only the active headless Turn's
matching `network.read` interaction as `allow_once`. It cannot enable Web,
create a Thread grant, approve another interaction, or bypass a hard denial.

## Full access confirmation

Full access is a two-step escalation:

1. `/permissions full_access` requires a selected Thread and creates a pending
   confirmation bound to `thread_id + permission generation`;
2. a separate interaction offers **Keep current permission mode** first and
   **Enable Full access for this thread** second.

The safe “keep” choice is first/default. While confirmation is pending, a new
Turn, direct command, `/new`, `/resume`, other state mutation, or another
external operation cannot pass the foreground gate. Snapshot commands and
cancellation are exceptions at the private Core boundary. The current Ink TUI
does not submit a newly typed snapshot through an active interaction; it queues
later input until the interaction/Operation is resolved.

Thread selection or any permission-mode transition invalidates the old
confirmation. At response time Core rechecks the interaction ID, selected
Thread, and permission generation while holding the resolving foreground lease;
only then does it emit the resolution and apply the mode. A delayed response
from an old Thread therefore cannot elevate a new one.

Full access lasts only for the selected Thread within the current Core session.
It is not written to user configuration or conversation state.

## Non-disableable boundaries

Permission mode cannot authorize:

- a structured filesystem-tool path that escapes the workspace or follows a
  symlink/reparse point (deleting a final link node without following it is the
  narrow exception);
- built-in filesystem-tool access to protected secret paths;
- filesystem-tool deletion of the workspace root, a filesystem root, `.git`,
  or protected sensitive content;
- a shell command rejected by the bounded command circuit breaker, including
  recognized root/workspace-root destructive deletion, elevation, shutdown,
  disk formatting, raw block-device writes, or a fork bomb;
- an invalid MCP schema/catalog or a stale MCP catalog generation;
- a stale interaction, operation, Thread, Turn, or permission generation.

These are product invariants rather than user preferences. The shell policy is
explicitly a circuit breaker for common catastrophic accidents, not a complete
malicious-code classifier. Full access still runs commands with the Awesome
process's host account. Use a VM, container, OS sandbox, or disposable checkout
when stronger containment is required.

The filesystem-tool secret/path boundary does not sandbox `execute`. An allowed
shell can name sensitive or outside-workspace files with host-account authority;
the sanitized child environment is not a filesystem access control.

## MCP and unknown extensions

MCP tools use capabilities outside the built-in
`workspace.read`/`workspace.write`/`workspace.delete`/`shell.execute` set. The
policy returns `ask` for every such call in all three modes. Approval authorizes
one invocation only; there is no Thread-wide MCP grant.

This is important even for a tool advertised as read-only. The MCP server is an
external process and its description is not an enforceable proof of side
effects. If transport loss occurs after dispatch, Awesome returns
`uncertain_outcome`, invalidates the catalog, and does not replay the call.

Local Memory capabilities are the other deliberate exception. `memory.read`
and `memory.write` are allowed outside the workspace matrix and do not prompt.
Mutating tool descriptions impose the explicit-current-user-request rule on the
model; runtime itself enforces active Agent Turn, matching workspace, content,
scope, redaction, and compare-and-swap checks rather than semantic intent
classification. See [Memory](../extensions/memory.md).

## Concurrency and lifecycle

Permission changes are foreground state mutations. They are rejected during an
active operation or while an unrelated interaction is pending. Tool approval
is the existing operation's continuation, so it bypasses the ordinary exclusive
gate only after matching all authority fields; otherwise the operation would
deadlock waiting on itself.

Shutdown stops new leases, cancels and waits for active operations/mutations,
then closes MCP, databases, and process resources. It does not persist a more
permissive mode for the next launch.
