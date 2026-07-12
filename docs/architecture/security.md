# Security boundary

Awesome is a trusted-workspace local tool, not an isolation boundary.

## Trust before project influence

Core canonicalizes the startup directory and checks user-owned trust state.
Until accepted, it does not read workspace configuration, project Skills, MCP
declarations, or run tools. Only Yes is durable; No exits the launch.

## File and process policy

File tools accept workspace-relative paths and reject canonical escapes,
absolute paths, unsafe symlink resolution, and sensitive credential/key paths.
Modifying file tools record controlled changes before mutation.

`execute` runs on the host with the workspace as working directory. It denies
privilege elevation, shutdown/reboot, disk formatting, and destructive
filesystem-root/workspace-root commands. File tools reject absolute paths and
workspace escapes; denial stops the call.
Tool schemas, timeouts, cancellation, output bounds, normalized errors, audit
summaries, and event emission are enforced by `ToolExecutor`.

## Permission policy

Workspace trust and tool permission answer different questions. Trust gates
all project-controlled configuration, instructions, Skills, and MCP discovery.
After trust, `PermissionPolicy` evaluates every model-driven capability:

- Request approval allows reads and asks for edits, deletes, shell execution,
  and unknown extension capabilities;
- a successful allow-once decision applies to one call;
- the Thread edit grant applies only to later ordinary workspace writes;
- Full access is explicitly confirmed and lasts only for the current Thread;
- hard denials run first and cannot be disabled by Full access.

The TUI renders the exact operation and target supplied by Core. It never
derives permission from prompt text and never executes the approved operation
itself.

## Extensions and data

Memory, Skills, repository instructions, and MCP output are untrusted context.
They cannot expand permissions. Secrets come only from the process environment
or user-owned `.env`, are redacted from events, and are never supplied by
workspace configuration.

There is no Docker sandbox today. If added later, it belongs below the Tool
Executor as an optional execution backend; workspace trust and tool policy
remain mandatory above it.
