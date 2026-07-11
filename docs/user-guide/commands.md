# Commands

## Launch flags

| Invocation | Result |
| --- | --- |
| `awesome` | Start a new thread in the current workspace. |
| `awesome --continue` | Resume the most recent thread in this workspace. |
| `awesome --resume` | Choose a recent thread. |
| `awesome --resume <thread_id>` | Resume the specified thread. |
| `awesome -V`, `awesome --version` | Print the numeric product version. |
| `awesome -h`, `awesome --help` | Print command-line help. |

No other public launch flags are supported.

## Application commands

| Command | Purpose |
| --- | --- |
| `/new` | Start a new thread. |
| `/resume [thread_id]` | Choose or resume a previous workspace thread. |
| `/context` | Show the active context manifest and budget. |
| `/compact` | Compact the current context now. |
| `/model [model]` | Show or select a supported Provider/model ID. |
| `/thinking [on\|off]` | Show the current mode with a selector, or set it explicitly. |
| `/workspace` | Show workspace identity and trust state. |
| `/diff` | Show the latest or selected Change Journal change set. |
| `/undo` | Undo the latest or selected reversible change set. |
| `/redo` | Redo the latest or selected undone change set. |
| `/tools` | List the effective built-in and extension tools. |
| `/skills` | List discovered Skills and diagnostics. |
| `/skill [auto\|off\|name]` | Show or select thread Skill mode. |
| `/mcp` | Show MCP server status. |
| `/memory` | Show memory status; see the [memory guide](memory-skills-mcp.md). |
| `/status` | Show the current product and thread status. |
| `/usage` | Show token and operation usage from the latest turn. |
| `/doctor` | Check configuration, embedded state, checkpoints, and Provider readiness. |
| `/config` | Show effective source and credential-presence diagnostics, never secret values. |

`/thinking` defaults to off. A bare `/thinking` reports the current value and
offers on/off choices.

## Skill-backed commands

`/init`, `/review`, `/debug`, `/test`, and `/commit` select the corresponding
bundled Skill and submit the remaining text as a normal Agent task. They do not
create a second execution system.

## Ink-local commands

| Command | Purpose |
| --- | --- |
| `/help [command]` | Show command help. |
| `/theme [system\|dark\|light]` | Show or select the TUI theme. |
| `/copy` | Copy the latest assistant answer. |
| `/quit` | Shut down Core and exit. |

`@path` adds a workspace path reference to a message. `! command` runs the
direct-shell interaction through the same Core execution policy; Ink never
executes tools itself.

## `/status` fields

`/status` renders:

- `Version`: one numeric value such as `1.0.0`;
- `Workspace`: the workspace path, without trust or Git-branch suffixes;
- `Thread` and resumable `Thread ID`;
- `Model`: the full Provider/model ID and configured state;
- `Modes`: thinking and Skill mode;
- `Memory`: local-file and Mem0 Cloud on/off states;
- MCP ready/degraded counts, active operation state, and configuration
  diagnostic count.

Context details and token/operation usage are intentionally separate in
`/context` and `/usage`.
