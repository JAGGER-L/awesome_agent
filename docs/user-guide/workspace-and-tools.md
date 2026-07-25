# Workspace and tools

## Workspace identity and trust

The directory in which `awesome` starts is the workspace. Awesome resolves it
to a canonical path and asks for trust on first use. A trusted workspace may
provide `.awesome/config.yaml`, `.awesome/skills/`, project instructions, and
MCP declarations. Declining exits before those sources or tools are loaded.

One active session holds both a canonical-path lease and a physical
filesystem-identity lease for the workspace. The first prevents a replacement
directory from reusing an active path; the second makes different path aliases
to the same directory share one runtime owner. User-facing aliases remain
valid, but two Awesome processes cannot concurrently recover or mutate the same
workspace through different spellings.

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
and sensitive paths such as secret files and private keys. On Windows they also
reject alternate data-stream syntax, trailing dots or spaces, reserved device
names, 8.3-style aliases, control characters, and invalid filename characters.
Those platform-specific spellings are not rejected merely for having the same
text on POSIX.

Before a built-in file operation reads or changes anything, Core binds the
workspace, parent directories, and the resolved target's existence and identity.
Opening performs a no-follow `lstat -> open -> fstat` comparison. Bounded reads
rewind the pinned regular-file descriptor, repeat the same bounded read, and
require both content and metadata to remain stable. `@path`, `read_file`, `ls`,
`glob`, and `grep` use the same primitives rather than reopening a checked
pathname. Regular files with multiple hard links are refused because reading or
modifying one name could affect an alias outside the visible workspace. Write
and edit use an atomic sibling replacement, but portable host filesystems do
not provide the required identity compare-and-swap. A replacement observed
before the final identity check is rejected; a same-privilege host process can
still replace a same-name target between that check and replace/remove, causing
the new in-workspace generation to be overwritten or deleted. Pinned parents
and no-follow operations keep that race inside the bound workspace and do not
follow the replacement as a link to an external target. On POSIX, another host
process can also move an already-open directory after the final reachability
check. Workloads requiring protection from a hostile concurrent process need
an external sandbox or mount boundary.

Before approval and again before process start, one dialect-aware command
policy examines the command, actual working directory, and workspace. It
handles known CMD, POSIX-shell, and PowerShell wrappers, compound commands,
pipelines, newlines, directory-changing segments, PowerShell encoded commands
and `Start-Process` elevation aliases, and selected literal Python
`-c` filesystem/process calls within bounded parser limits. Unparseable input is
denied. Privilege elevation, shutdown/reboot, disk formatting, block-device
overwrite, fork bombs, and recursive filesystem-root or workspace-root deletion
remain hard denied in every mode and for direct `!` commands.

This hard-deny layer is a non-disableable circuit breaker for recognizable
accidents. It does not claim to detect arbitrary malicious obfuscation and is
not an operating-system sandbox. `execute` runs on the local host. Its requested
command timeout governs the process lifecycle, with a bounded cleanup budget
for process-tree termination and pipe draining. Core waits for the root command
separately from stdout/stderr EOF, so a descendant that inherits an output pipe
can make that output truncated but cannot keep the call open forever. On
Windows a Core-level kill-on-close Job Object covers Core exit, while every
`execute` uses a nested command job whose waiting supervisor is assigned before
the target can spawn. On POSIX a lease-bound supervisor owns each command
process group. A command that intentionally daemonizes into a new session can
escape the POSIX group. These are cleanup guarantees, not restrictions on host
access. A timed-out or disconnected external side effect may have an uncertain
outcome and is never replayed transparently.

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

Undo and redo preflight every affected path before restoring any of them. Core
records all pending restore intents, applies them through the same bound
workspace tree, and changes the ChangeSet state only after every path matches.
An error before that commit rolls already-restored paths back; if that cannot
be verified, pending evidence remains. If Core exits mid-operation, startup
recovery either verifies the committed result or rolls an uncommitted partial
result back; ambiguous identities, content, or lifecycle leave the pending
record intact instead of guessing.

The journal records the before and after node types separately, so replacing a
directory or symlink with a file remains reversible. Sealing one completed Turn
only reconciles that Turn's ChangeSet; an unrelated pending ChangeSet is left
for its own recovery path.

Shell effects are not snapshots. A mixed file/shell turn may be only partially
reversible, and an execute-only turn may have no reversible change. Workspace
files and their diff are the artifact; there is no separate artifact system.
