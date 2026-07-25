# Security boundary

Awesome is a trusted-workspace local tool, not an isolation boundary.

## Trust before project influence

Core canonicalizes the startup directory and checks user-owned trust state.
Until accepted, it does not read workspace configuration, project Skills, MCP
declarations, or run tools. Only Yes is durable; No exits the launch.

After trust, the root `AGENTS.md` is loaded once through `lstat`, a bounded open,
and post-open identity verification. Links/reparse points, path escape, NUL or
non-UTF-8 content, replacement during read, files above 32 KiB, and content
above the smaller of 8,192 tokens or 10% of the effective input budget are
ignored whole. The resulting diagnostic does not make configuration invalid
and remains visible in Welcome, the status line, and `/doctor`.

## File and process policy

File tools accept workspace-relative paths and reject canonical escapes,
absolute paths, unsafe symlink resolution, and sensitive credential/key paths.
On Windows they also reject path spellings whose object identity is ambiguous:
alternate data streams, trailing dots or spaces, reserved DOS device names,
8.3-style aliases, control characters, and invalid filename characters. Those
spellings remain ordinary names on POSIX where applicable; the platform rule is
not projected onto a different filesystem dialect.

Containment by pathname is not enough because an attacker or concurrent
process can replace a checked parent or final object before use. File
transactions therefore pin the workspace and each parent directory, bind the
resolved target's existence and identity, compare pre-open and post-open
identity, use descriptor-relative no-follow operations where the platform
supports them, and recheck reachability around the operation. Bounded reads
also compare the opened identity, type, link count, size, and modification time
after consuming the descriptor. Regular files with more than one hard link are
refused because a workspace pathname cannot prove where every alias points.
Write and edit use an atomic sibling replacement. Recursive delete inventories
and binds the whole tree first; any nested symlink, junction, reparse directory,
or hard-linked file, and any capacity violation or identity change observable
during inventory and preflight, fails before the first deletion.

These checks are a fail-closed defense against accidental replacement and the
observable races available to the process; they are not a kernel-enforced
workspace jail. A same-privilege host process can replace a same-name target
after the final identity check but before the operating-system replace or
remove call. Pinned, no-follow parent operations keep that effect inside the
workspace, but the new in-workspace generation can still be overwritten or
deleted. On POSIX, another host process can also move an already-open parent
after the final reachability check while descriptor-relative work still refers
to that same object. Preventing these adversarial races requires a platform
compare-and-swap primitive, an exclusive workspace-writer protocol, or an OS
sandbox or mount namespace, none of which Awesome currently provides.

The same primitives protect Change Journal restoration. Undo and redo bind and
preflight every path, persist all pending intents, then restore through one
pinned workspace-tree transaction. If an error occurs before the ChangeSet
lifecycle commit, already-restored paths are rolled back in that same bound
tree. If rollback cannot be verified, the pending evidence is not cleared.
Recovery verifies and finalizes a committed operation, or rolls an uncommitted
partial operation back to its recorded before-state. Conflicting or
unverifiable state remains pending for explicit diagnosis; recovery never
chooses a path from fresh string resolution and assumes it is the old object.
Before and after node types are recorded independently, so restoration does not
reinterpret a directory or symlink snapshot as a regular file. Mutation IDs
make a committed ordinary mutation idempotent across the metadata/pending
cleanup crash window. Older completed records without that optional identity
remain readable, while an indistinguishable legacy record plus pending intent
is retained as an explicit conflict.

`execute` runs on the host with the actual working directory supplied to one
pure command policy before approval and again before spawn. Bounded parsers for
CMD, POSIX shell, and PowerShell expand known wrappers, compound commands,
pipelines, and newlines. Compound inspection carries a conservative set of
possible working directories across `cd`/`chdir`/`Set-Location` segments and
stateful wrappers, so a later relative delete is evaluated where the shell may
actually run it. PowerShell `Start-Process` elevation aliases such as `saps`
are treated as the underlying command. The policy normalizes executable paths and suffixes,
decodes PowerShell encoded commands, and inspects selected literal Python `-c`
calls. It denies input it cannot parse safely, along with privilege elevation,
shutdown/reboot, disk formatting, block-device overwrite, fork bombs, and
recursive filesystem-root/workspace-root deletion.

This is a non-disableable accident circuit breaker, not a claim to recognize
arbitrary malicious obfuscation. Recursive delete separately refuses an
inventory containing any symlink, junction, or reparse directory before the
first mutation. File tools reject absolute paths and workspace escapes; denial
stops the call.
Tool schemas, timeouts, cancellation, output bounds, normalized errors, audit
summaries, and event emission are enforced by `ToolExecutor`.

`ProcessRunner` treats root-process lifetime and pipe lifetime as separate
bounded phases. The requested timeout covers spawn and the root command;
termination, force-kill, and stdout/stderr draining have bounded cleanup
budgets. If a descendant inherits a pipe after the root exits, Core cancels the
reader after the drain deadline, marks that stream truncated, and continues
cleanup instead of waiting forever. Cancellation performs the same bounded
cleanup and then re-raises the original cancellation.

On POSIX, Core has explicit process-group ownership and each shell command is
started by a separate session supervisor. Core owns a lease-pipe write end;
abnormal Core exit closes it, causing the supervisor to terminate its remaining
process group. A command that deliberately calls `setsid()` or otherwise
daemonizes out of that group is not contained by this mechanism. On Windows,
Core installs a kill-on-close lifetime Job Object and assigns itself before
async startup; if that cannot be established, Core exits rather than running
without its lifetime invariant. Every `execute` creates a nested kill-on-close
command job and starts a private supervisor in a waiting state. Core assigns the
supervisor to that job before releasing it to spawn the requested executable,
so descendants inherit the cleanup domain without a spawn race. Root
completion, timeout, cancellation, and setup failure terminate the command job;
the outer lifetime job remains the abnormal-Core-exit backstop. These mechanisms
limit orphaned processes; they do not restrict what a successfully running
process can read, write, or access.

## Permission policy

Workspace trust and tool permission answer different questions. Trust gates
all project-controlled configuration, instructions, Skills, and MCP discovery.
After trust, `PermissionPolicy` evaluates every model-driven capability:

- Request approval allows reads and asks for edits, deletes, shell execution,
  MCP, and unknown extension capabilities;
- Accept edits additionally allows ordinary workspace writes, but still asks
  for deletes, shell execution, MCP, and unknown capabilities;
- a successful allow-once decision applies to one call;
- the Thread edit grant applies only to later ordinary workspace writes;
- Full access is explicitly confirmed against the selected Thread and current
  permission generation, and allows only known built-in local writes, deletes,
  and shell execution;
- MCP and unknown future capabilities always require one-call approval;
- Thread or mode changes invalidate stale Full access confirmation and clear
  temporary grants;
- hard denials run first and cannot be disabled by Full access.

The TUI renders the exact operation and target supplied by Core. It never
derives permission from prompt text and never executes the approved operation
itself.

## Extensions and data

Memory, Skills, repository instructions, and MCP output are untrusted context.
They cannot expand permissions. Secrets come only from the process environment
or user-owned `.env`; `/auth` persists the explicitly selected source. If that
source becomes unavailable, Core reports it and never falls back silently.
Secret values are redacted from events and never supplied by workspace
configuration.

Workspace Skills reject links and reparse points at every discovery and load
component and verify file identity after open. MCP catalogs are validated and
installed atomically only after all pagination completes; cursor cycles and
page, tool-count, byte, depth, and deadline limits fail the whole catalog.
Local-only references and generation-bound handlers prevent network schema
resolution, partial namespace registration, and stale validator reuse. Input
arguments are validated before approval and remote I/O. A declared
`outputSchema` validates structured output before presentation; missing or
invalid structured output becomes a bounded failure without exposing the
arguments or schema. Structured JSON is limited to 64 KiB, 4,096 nodes, and 64
levels before schema traversal; text/media results are limited to 1,024 content
blocks before synchronous rendering. MCP timeout or transport loss is an uncertain outcome and
is never transparently reconnected or replayed in the same Turn.

There is no Docker or other operating-system sandbox today, including in Full
access. If one is added later, it belongs below the Tool Executor as an optional
execution backend; workspace trust, approval, and the command circuit breaker
remain mandatory policy layers above it but are not substitutes for isolation.
This approval-versus-isolation distinction is consistent with the layered
models documented by [OpenAI](https://learn.chatgpt.com/docs/sandboxing#how-permissions-work),
[Claude Code](https://code.claude.com/docs/en/permissions), and
[Hermes](https://hermes-agent.nousresearch.com/docs/user-guide/security/).
