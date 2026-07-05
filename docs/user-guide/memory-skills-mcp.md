# Memory, Skills, And MCP

## Memory

Memory is optional. When enabled, bounded memory context may be added to a
conversation turn as untrusted reference material. Memory cannot grant tools,
approve commands, change sandbox policy, or override higher-priority
instructions.

Use `/memory` to inspect and manage memory from the local TUI.

## Skills

Skills provide instructions and optional context for a turn. Use `/skills` to
stage skills for the next turn. Staged skills apply to that turn and then clear.

Skills can request tools, but they do not grant authority. Tool visibility and
execution still go through runtime capability policy.

## Visibility Versus Execution

A discovered skill or MCP tool can appear in inventory before it is executable
in the current turn. Execution still requires route capability policy, registry
registration, and approval policy.

## MCP

Use `/mcp` to inspect configured MCP sources and health. MCP tools are hidden
unless policy exposes them, and execution still passes through approval,
timeout, cancellation, redaction, and audit boundaries.
