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
filesystem-root/workspace-root commands. Referencing an absolute path outside
the workspace requires an `allow_once` interaction; denial stops that call.
Tool schemas, timeouts, cancellation, output bounds, normalized errors, audit
summaries, and event emission are enforced by `ToolExecutor`.

## Extensions and data

Memory, Skills, repository instructions, and MCP output are untrusted context.
They cannot expand permissions. Secrets come only from the process environment
or user-owned `.env`, are redacted from events, and are never supplied by
workspace configuration.

There is no Docker sandbox today. If added later, it belongs below the Tool
Executor as an optional execution backend; workspace trust and tool policy
remain mandatory above it.
