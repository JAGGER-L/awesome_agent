# Workspace and tools

## Workspace identity and trust

The directory in which `awesome` starts is the workspace. Awesome resolves it
to a canonical path and asks for trust on first use. A trusted workspace may
provide `.awesome/config.yaml`, `.awesome/skills/`, project instructions, and
MCP declarations. Declining exits before those sources or tools are loaded.

After trust, Core reads only a plain root `AGENTS.md` and freezes it for the
session. The read is limited to 32 KiB and to the smaller of 8,192 tokens or
10% of the effective input budget. Links and reparse points, path escapes,
binary or non-UTF-8 content, replacement during the read, and any limit excess
cause the whole file to be ignored. Awesome does not truncate instructions that
could change meaning. The structured diagnostic remains visible in Welcome,
the status line, and `/doctor`; a missing file is normal.

Trust grants normal coding access inside that workspace; it is not an
operating-system sandbox and does not grant access to every path on the host.

## Permission modes and approval

Trust answers whether project-controlled content may influence Awesome.
Permission mode separately decides whether an already trusted operation needs
confirmation. `/permissions` exposes three session-only modes:

| Capability | Request approval | Accept edits | Full access |
| --- | --- | --- | --- |
| Read | Allow | Allow | Allow |
| Create or modify workspace files | Ask | Allow | Allow |
| Delete | Ask | Ask | Allow |
| Shell | Ask | Ask | Allow |
| MCP or unknown extension | Ask | Ask | Ask |

Request approval is the default. Accept edits automates only ordinary
`workspace.write` operations. Full access requires a warning confirmation tied
to the selected Thread and current permission generation; keeping Request
approval is the preselected safe choice. Switching Threads or modes invalidates
an old confirmation and clears temporary capability grants. Full access raises
only known built-in local capabilities and never makes MCP or a future unknown
capability implicit.

An edit approval offers Yes, Yes for all edits during this session, and No.
The session edit grant covers later ordinary writes only. Shell commands and
other capabilities keep their own policy. Escape denies the current request.

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

Before approval and again before process start, one dialect-aware command
policy examines the command, actual working directory, and workspace. It
handles known CMD, POSIX-shell, and PowerShell wrappers, compound commands,
pipelines, newlines, PowerShell encoded commands, and selected literal Python
`-c` filesystem/process calls within bounded parser limits. Unparseable input is
denied. Privilege elevation, shutdown/reboot, disk formatting, block-device
overwrite, fork bombs, and recursive filesystem-root or workspace-root deletion
remain hard denied in every mode and for direct `!` commands.

This hard-deny layer is a non-disableable circuit breaker for recognizable
accidents. It does not claim to detect arbitrary malicious obfuscation and is
not an operating-system sandbox. `execute` runs on the local host. Its requested
command timeout governs the process lifecycle, with a bounded cleanup budget
for process-tree termination and pipe draining. A timed-out or disconnected
external side effect may have an uncertain outcome and is never replayed
transparently.

File tools never accept an absolute path or workspace escape. Before recursive
delete builds its inventory, it rejects any nested symlink, junction, or other
reparse directory, so the failure occurs before either workspace or external
files are changed. There is no Docker sandbox today; a future Docker execution
backend is deferred until real demand.

Tool activity keeps its event order. All Tool calls between two assistant
segments form one sequence, even when Thinking occurs between calls. The whole
sequence folds to one row; Ctrl+O reveals each Tool's target, bounded result,
duration, and known omitted-entry count. Current-session details are not
durable history, so resumed Threads show stored Tool summaries only.

Completed answers render Markdown tables and readable inline or block formulas
such as `S = πr²`. Streaming output holds incomplete structural blocks until
they can render stably. Advanced LaTeX that cannot be represented faithfully in
a terminal remains visible as source text rather than disappearing.

## Change Journal

`write_file`, `edit_file`, and `delete` record before/after data in one change
set per modifying turn. `/diff`, `/undo`, and `/redo` operate on those change
sets and refuse to overwrite later conflicting edits.

Shell effects are not snapshots. A mixed file/shell turn may be only partially
reversible, and an execute-only turn may have no reversible change. Workspace
files and their diff are the artifact; there is no separate artifact system.
