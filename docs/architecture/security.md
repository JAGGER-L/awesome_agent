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
Modifying file tools record controlled changes before mutation.

`execute` runs on the host with the actual working directory supplied to one
pure command policy before approval and again before spawn. Bounded parsers for
CMD, POSIX shell, and PowerShell expand known wrappers, compound commands,
pipelines, and newlines. The policy normalizes executable paths and suffixes,
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
installed atomically; local-only references and generation-bound handlers
prevent network schema resolution, partial namespace registration, and stale
validator reuse. MCP timeout or transport loss is an uncertain outcome and is
never transparently reconnected or replayed in the same Turn.

There is no Docker or other operating-system sandbox today, including in Full
access. If one is added later, it belongs below the Tool Executor as an optional
execution backend; workspace trust, approval, and the command circuit breaker
remain mandatory policy layers above it but are not substitutes for isolation.
This approval-versus-isolation distinction is consistent with the layered
models documented by [OpenAI](https://learn.chatgpt.com/docs/sandboxing#how-permissions-work),
[Claude Code](https://code.claude.com/docs/en/permissions), and
[Hermes](https://hermes-agent.nousresearch.com/docs/user-guide/security/).
