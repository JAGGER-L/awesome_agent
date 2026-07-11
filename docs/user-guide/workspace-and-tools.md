# Workspace and tools

## Workspace identity and trust

The directory in which `awesome` starts is the workspace. Awesome resolves it
to a canonical path and asks for trust on first use. A trusted workspace may
provide `.awesome/config.yaml`, `.awesome/skills/`, project instructions, and
MCP declarations. Declining exits before those sources or tools are loaded.

Trust grants normal coding access inside that workspace; it is not an
operating-system sandbox and does not grant access to every path on the host.

## Initial default tools

| Tool | Contract |
| --- | --- |
| `ls` | List files in a directory. |
| `read_file` | Read file contents. |
| `write_file` | Create a new file, or overwrite an existing one. |
| `edit_file` | Perform exact string replacements in files. |
| `delete` | Delete a file, or a directory and its contents recursively. |
| `glob` | Find files matching a glob pattern. |
| `grep` | Search file contents. |
| `execute` | Run shell commands. |

This is the initial built-in baseline, not a permanent tool-count ceiling. MCP,
Skills, and user extensions may register namespaced tools through the same
registry and executor.

## Enforcement boundary

All model-driven tools pass through one registry and executor for input
validation, workspace policy, normalized results, events, and change capture.
File tools reject absolute paths, workspace escapes, unsafe symlink results,
and sensitive paths such as secret files and private keys.

`execute` runs on the local host in the workspace. Privilege elevation,
shutdown, disk formatting, destructive filesystem-root/workspace-root commands
are denied. An absolute path outside the workspace requires an explicit
allow-once interaction. There is no Docker sandbox today; a future Docker
execution backend is deferred until real demand.

## Change Journal

`write_file`, `edit_file`, and `delete` record before/after data in one change
set per modifying turn. `/diff`, `/undo`, and `/redo` operate on those change
sets and refuse to overwrite later conflicting edits.

Shell effects are not snapshots. A mixed file/shell turn may be only partially
reversible, and an execute-only turn may have no reversible change. Workspace
files and their diff are the artifact; there is no separate artifact system.
