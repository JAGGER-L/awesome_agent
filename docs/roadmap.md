# Roadmap

Awesome's roadmap focuses on making terminal coding workflows more useful while
keeping one shared Python Core and one consistent tool-policy boundary.

## Current Foundation

- terminal interaction through the Ink + React TUI;
- workspace file and command tools with visible results;
- DeepSeek and Kimi model providers;
- resumable Threads and local checkpoints;
- Skills and MCP extensions;
- independently optional local file Memory and Mem0 Cloud;
- Change Journal support for diff, undo, and redo.

## Documentation and Documentation Site

Improve task-oriented guides, examples, troubleshooting, and architecture
references, then publish them through a searchable documentation site.

## One-command Skills Installation

Let users discover and install Skills with one command while preserving the
current manifest validation, source precedence, workspace trust, and tool
policy.

## Multi-Agent

Add scoped delegation for tasks that benefit from parallel or specialized work.
Sub-Agents will receive explicit context and tool boundaries while the current
Agent Core remains responsible for the user-facing Turn.

## More Model Providers

Add providers that implement the shared model contract without moving
provider-specific behavior into Agent nodes, tools, or the TUI.

## Search Tools

Add optional Web Search and Web Fetch tools through the same Registry, Policy,
Executor, result, and event path as every other tool.

## More Memory Providers

Introduce additional external memory services after a second concrete adapter
justifies a shared provider contract. Local Memory remains independent.

## Cron Tasks

Support scheduled tasks that reuse the same Agent Core, Tools, Skills, Memory,
workspace trust, and execution budgets rather than creating a separate task
engine.

## Gateway Messaging

Allow messaging platforms to submit work and receive progress or results through
an adapter around Application contracts and events. Gateway integrations will
not duplicate Agent behavior.

## Optional Docker Tool Backend

Add Docker as an optional Tool Executor backend for users who want stronger
process isolation. Workspace trust and tool policy remain mandatory above the
backend, and normal local execution stays available.
