# Permissions and Safety

This page is for choosing how much confirmation Awesome should require in a
trusted project. It explains what each control protects, how authority expires,
and which risks remain outside the product boundary.

## Start with the Threat, Not the Toggle

Four controls address four different questions:

```text
Workspace trust     Is project-controlled content allowed to load?
Permission mode     Must this trusted operation ask the user?
Hard denial         Is this recognized operation always too dangerous?
External isolation  What can the process reach on the host at all?
```

Awesome implements the first three. It does not currently provide the fourth.
Permission prompts and command analysis are therefore controls against
mistakes and unintended authority, not a containment boundary for hostile
code.

## Workspace Trust

Trust is requested before Workspace configuration, root instructions, Skills,
MCP declarations, or normal tools are activated. Confirm the displayed launch
path. Core canonicalizes that path and stores trust under a canonical Workspace
key, so two aliases that resolve to the same canonical directory share the same
persistent trust record. Declining exits and does not save a denial, so the
next launch asks again.

The live process separately records the physical root identity, acquires
path/entity leases, and checks that the root has not been replaced. That
identity is session safety state, not the persisted trust key. A replacement
invalidates the active session; it does not automatically create a new durable
trust decision. Trusting one canonical Workspace does not trust another one or
make paths outside it available to built-in file tools.

## Permission Modes

Run `/permissions` to inspect or choose the active mode. The three modes are:

- **Request approval**: allow reads; ask before built-in creates, edits,
  deletes, and shell execution. This is the default.
- **Accept edits**: additionally allow ordinary built-in file creation and
  modification. Deletes and shell commands still ask.
- **Full access**: allow known built-in local writes, deletes, and shell
  execution after an explicit warning confirmation.

MCP and unknown extension capabilities ask once per call in every mode. Built-in
Memory operations follow their own explicit enablement and mutation policy.
The canonical capability matrix is maintained in
[Permission modes](../reference/permission-modes.md).

## Choosing a Mode

Use Request approval when exploring an unfamiliar codebase, reviewing a risky
change, or learning what tools a task requires. Use Accept edits for ordinary
implementation work when you want to inspect every delete and command but not
confirm each edit. Use Full access only for a Thread whose goal and host
environment you understand well enough to accept unprompted local execution.

Example progression:

```text
/permissions request_approval
```

Inspect the plan and first proposed change, then opt into ordinary edits:

```text
/permissions accept_edits
```

Return to the conservative baseline before starting unrelated work:

```text
/permissions request_approval
```

Changing modes clears temporary capability grants and advances the permission
generation. Selecting another Thread also resets the permission session to
Request approval.

## Headless Authority

`awesome run "<prompt>"` uses these same controls; non-interactive execution is
not a separate permission path. It creates a new Thread unless
`--thread <id>` selects an existing one. `--trust-workspace` accepts trust for
the canonical launch Workspace through the normal startup interaction. Without
that explicit flag, required trust remains unresolved and the command exits
with code 3.

`--permission-mode request_approval|accept_edits|full_access` requests the
normal mode for the selected Thread and current process. Supplying
`full_access` is the explicit headless confirmation of the same warning, not a
way around the warning's invariants: authority remains Thread/session scoped,
and path checks, limits, extension approvals, and hard denials still apply. If
a later tool call needs an approval that cannot be resolved non-interactively,
the runner requests cancellation, writes no partial answer to stdout, and
exits with code 3.

`--allow-network` authorizes this process to resolve only the active headless
Turn's exact `network.read` prompt as `allow_once`. It does not make disabled
Web available, cannot create a Thread grant or resolve another interaction,
and cannot bypass hard denial.

## Approval Semantics

A write approval can offer:

- **Yes**: allow this call only;
- **Yes, allow all edits during this session**: allow later
  `workspace.write` calls in the selected Thread/session;
- **No**: deny the call.

The temporary write grant never includes delete or shell execution. Delete,
shell, MCP, and unknown capabilities only offer a one-call decision. Escape
denies the current approval.

`network.read` is different from local Full access: its first call asks in
every permission mode. The choices are **No** (default), **Allow once**, and
**Allow for this Thread**. A Thread network grant is cleared when the selected
Thread changes, the runtime is rebuilt, the permission mode changes,
`/web revoke` or `/web off` runs, or Awesome exits.

Each tool approval is bound to its Thread, Turn, Operation, and interaction
identity. A response is accepted at most once and only while those facts are
still current. A stale UI response cannot authorize a replacement Turn.

## Full Access Confirmation

Typing `/permissions full_access` does not immediately change the mode. A
Thread must already be selected, no other Operation or interaction may be
active, and the warning prompt defaults to **Keep current permission mode**.

The confirmation binds the selected Thread and current permission generation.
Switching Thread or mode invalidates the old prompt. Full access is session
authority, not a permanent repository rule, and it still cannot bypass:

- sensitive-path, Workspace-escape, link, reparse, hard-link, and identity
  checks for built-in file tools;
- recognized command hard denials;
- MCP and unknown-extension approval;
- tool argument validation and resource limits.

## Direct `!` Commands

`! command` means the user has explicitly selected that command, so Core runs
the `execute` tool with direct authority rather than asking for shell approval.
This behavior is independent of the selected permission mode. The command is
still subject to the same hard-deny evaluation before approval would occur and
again before process start, plus the same environment filtering, timeout,
process cleanup, redaction, and journal observation.

Use direct commands only for commands you can review yourself:

```text
! git status --short
! uv run pytest tests/unit/example.py -q
```

Do not use `!` as a way to avoid understanding a generated command. Ask Awesome
to explain a command first when its effects are unclear.

## Hard Denials

The command circuit breaker rejects recognizable catastrophic operations in
all modes and for direct commands. Its scope includes privilege elevation,
shutdown/reboot, disk formatting or block-device overwrite, fork bombs, and
recursive deletion of a filesystem root or the Workspace root. It parses
bounded CMD, POSIX shell, and PowerShell forms, including selected wrappers,
compound commands, encoded PowerShell, and literal Python command calls.

Input that cannot be inspected safely is denied. This is intentionally
conservative, but it is not a general malicious-code detector: a sufficiently
indirect program can perform effects that are not visible in the command
string.

## What Permissions Do Not Protect

Once a host process is allowed to run, it may use the current user's network,
credentials, and filesystem authority. Environment filtering removes common
secret variable suffixes from `execute`, but programs can discover data through
other host channels. Process-tree cleanup limits orphans but is not
containment. An external service may complete an action after the connection
times out.

Use a VM, container, restricted OS account, mount boundary, or managed sandbox
when the repository, command, dependency, or MCP server is not trusted. Grant
that environment only the credentials and paths required for the task.

## If a Prompt Looks Wrong

Choose No or press Escape, then inspect `/status`, `/tools`, and the original
request. A pending interaction blocks new mutations until resolved. If the
target or capability is unexpected, treat it as a task mismatch rather than
raising the mode.

Continue with [Tools and shell execution](tools-and-shell.md) and the focused
[security architecture](../architecture/README.md). Exact policy outcomes are
in the [permission reference](../reference/permission-modes.md).
