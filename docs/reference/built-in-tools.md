# Built-in tool reference

Tools are the only path from model intent to workspace reads, file mutations,
or host execution. The model submits a name plus JSON arguments; Core validates
the registered schema, evaluates capability policy, obtains approval when
needed, invokes the handler under a deadline, records one terminal activity,
and emits one terminal tool event.

```text
Model tool call
       |
       v
registered model validation -> lexical hard checks -> permission policy
                                              |          |
                                            allow       ask
                                              |          |
                                              |   bound user interaction
                                              |          |
                                              +----+-----+ approved
                                                   |
                                                   v
                                      handler safety -> deadline
                                                   |
                                                   v
                                      Change Journal / audit
                                                   |
                                                   v
                                  bounded result + terminal event
```

The catalog always contains four read tools and the two Skill support tools.
It contains file mutation and shell tools in the normal local composition.
Local Memory tools appear only while Local Memory is enabled, and valid MCP
catalogs add namespaced tools. Dynamic MCP behavior is documented separately
in [MCP](../extensions/mcp.md).

## Common request and result contract

A request has a unique `call_id`, a registered `tool_name`, and an arguments
object. Unknown names return `not_found`; schema mismatches return the generic
`invalid_arguments` error without echoing sensitive arguments or schemas.

The current built-in and Skill-support argument models are typed but not a
uniform strict/closed JSON contract. Most use Pydantic defaults, which ignore
unknown object fields and coerce compatible scalars (for example, `"5"` to an
integer or `"false"` to a boolean). Memory models reject unknown fields but
still permit scalar coercion. MCP tools instead follow the server's compiled
JSON Schema. Callers should send only the documented fields with native JSON
types; making every local tool model `extra="forbid"` and strict is a known
runtime contract-hardening task.

A result has:

- the matching `call_id` and `tool_name`;
- `status`, either `success` or `error`;
- at most 30,000 characters of model-visible `content`;
- bounded structured `metadata`;
- an error code/message only for an error result;
- presentation fields used by the TUI: verb, target, outcome, summary, bounded
  detail, truncation count, and duration.

The stable built-in error codes are `invalid_arguments`, `not_found`,
`workspace_not_trusted`, `workspace_escape`, `permission_denied`, `conflict`,
`timeout`, `execution_failed`, `uncertain_outcome`, `memory_disabled`,
`memory_conflict`, `memory_rejected`, and `cancelled`. `uncertain_outcome` is
primarily an MCP boundary: it means an external side effect may have happened
and must not be replayed automatically.

Ordinary handlers have a 30-second outer deadline. `execute` supplies a dynamic
deadline described below. Cancellation is propagated after bounded cleanup;
it is not converted into a normal error result.

## Workspace path rules

Structured filesystem-tool `path` arguments, and the `execute` working
directory, are workspace-relative. Absolute paths, `..` escapes, and ambiguous
host syntax are rejected. Filesystem reads and mutations pin and recheck
workspace/path identities so a later directory swap fails instead of silently
redirecting the intended file operation.

Filesystem tools never *follow* a symlink, junction, or other reparse point.
There is one deliberate distinction: on platforms where it is safe, `delete`
may remove a final symlink node itself without following its target. A link in
a parent component, a nested link during recursive inventory, and a Windows
directory reparse target are rejected before deletion begins.

Sensitive paths are unavailable to built-in **filesystem** tools. This includes
`.env` and its non-example variants, `.ssh`, common private-key suffixes/names,
credential or secret path components, and AWS credential files. Directory
listing also hides `.git`; deletion always protects `.git` and sensitive paths.

These checks do not constrain what an approved host shell can name. `execute`
can read a sensitive or outside-workspace file using the Awesome process's host
account, especially in Full access. Scrubbing common secret environment
variable names reduces inheritance but provides no filesystem isolation. The
working-directory handler resolves identity and reruns the command circuit
breaker immediately before calling the runner, but it passes a pathname to the
OS spawn API rather than a pinned directory handle; a same-privilege concurrent
replacement remains a TOCTOU boundary. See
[permission modes](permission-modes.md).

## Read tools

### `glob`

Find regular workspace files using a relative glob.

| Argument | Type | Default | Limits/semantics |
| --- | --- | --- | --- |
| `pattern` | string | required | 1–500 characters; cannot be absolute or contain `..` |
| `path` | string | `.` | Existing directory used as the search root |
| `max_results` | integer | 200 | 1–1,000 |

The result content is one relative path per line. Metadata contains the path
array and `truncated`. Enumeration prunes `.git`, Python/pytest/mypy/Ruff
caches, `.venv`, `venv`, `build`, `dist`, and `node_modules`.

```json
{"pattern":"tests/**/*.py","path":".","max_results":250}
```

### `grep`

Search UTF-8 text files line by line.

It uses the same file enumerator and default pruned-directory set as `glob`.

| Argument | Type | Default | Limits/semantics |
| --- | --- | --- | --- |
| `pattern` | string | required | 1–1,000 characters |
| `path` | string | `.` | Existing search-root directory |
| `include` | string or omitted | omitted | Optional relative file glob, at most 500 characters |
| `regex` | boolean | `true` | Treat `pattern` as a Python regular expression when true |
| `case_sensitive` | boolean | `true` | Controls regex flags or literal comparison |
| `max_results` | integer | 100 | 1–500 matching lines |

Each line is rendered as `path:line: text`; an individual matched line is
bounded to 2,000 characters. Binary, non-UTF-8, and files larger than 1 MiB are
skipped. Metadata contains structured path/line/text records and `truncated`.

```json
{"pattern":"ForegroundArbiter","path":"src","include":"**/*.py"}
```

### `ls`

List one directory without recursion.

| Argument | Type | Default | Limits/semantics |
| --- | --- | --- | --- |
| `path` | string | `.` | Existing directory |
| `max_entries` | integer | 200 | 1–1,000 |

Content contains `type<TAB>path`. Metadata contains `{name, path, type}` entries
and `truncated`. The listing is identity-bound and excludes protected entries.

### `read_file`

Read a bounded line range from one UTF-8 regular file.

| Argument | Type | Default | Limits/semantics |
| --- | --- | --- | --- |
| `path` | string | required | Existing regular file, at most 1 MiB |
| `start_line` | integer | 1 | At least 1 |
| `end_line` | integer or omitted | end of file | At least 1 |

At most 500 lines and 30,000 rendered characters are returned. Lines include
one-based prefixes such as `17: content`. Metadata reports requested/actual
range, total lines, and truncation. NUL-containing, non-UTF-8, oversized, or
identity-changing files fail instead of being decoded heuristically.

## File mutation tools

Every mutation runs inside an open ChangeSet. Before/after bytes, modes, and
identities are captured through the Change Journal so `/diff`, `/undo`, crash
reconciliation, and audit see the same mutation boundary. A ChangeSet can
contain at most 1,000 filesystem nodes and 50 MiB of captured content.

### `write_file`

Create or atomically replace a UTF-8 file.

| Argument | Type | Limits |
| --- | --- | --- |
| `path` | string | Workspace-relative target |
| `content` | string | At most 1,000,000 characters |

If the target exists, it must be a regular file and its current content must fit
the ChangeSet byte limit. The original mode is preserved. Parent components
must already exist; link traversal is never used.

```json
{"path":"notes/review.md","content":"# Review\n\nReady.\n"}
```

### `edit_file`

Apply an exact textual replacement, preserving file mode.

| Argument | Type | Default | Limits/semantics |
| --- | --- | --- | --- |
| `path` | string | required | Existing UTF-8 regular file, at most 1 MiB |
| `old_string` | string | required | 1–200,000 characters |
| `new_string` | string | required | 0–200,000 characters |
| `replace_all` | boolean | `false` | Replace every exact occurrence when true |

Zero matches returns `not_found`. Multiple matches with `replace_all: false`
return `conflict`; the tool does not guess which occurrence the model meant.

### `delete`

Delete one file or recursively delete one directory.

| Argument | Type | Limits/semantics |
| --- | --- | --- |
| `path` | string | Existing non-root workspace path |

Before the first removal, Core inventories the complete subtree and validates
every node against protected paths, link/reparse rules, identity, the 1,000-node
limit, and the 50 MiB capture limit. Any nested junction/reparse directory makes
the inventory fail with **zero deletion**. Workspace root, filesystem root,
`.git`, and sensitive targets cannot be deleted in any permission mode.

Deletion is a distinct `workspace.delete` capability. Accept edits therefore
does not silently broaden from “edit files” to “remove a tree.”

## Shell execution: `execute`

Run a host-shell command from a validated workspace directory.

| Argument | Type | Default | Limits/semantics |
| --- | --- | --- | --- |
| `command` | string | required | 1–8,000 characters |
| `cwd` | string | `.` | Existing workspace directory; cannot be a link |
| `timeout_seconds` | number | 60 | Greater than 0, at most 600 |
| `max_output_chars` | integer | 30,000 | 1,000–200,000 independently for bounded runner capture |

On Windows the runtime invokes `cmd.exe /d /s /c`; on POSIX it invokes
`/bin/sh -lc`. The command policy still understands CMD, POSIX shell, and
PowerShell payloads so known wrappers are recursively inspected. The same pure
policy is evaluated before approval and immediately before process launch.

The policy normalizes executable paths, case, and Windows executable suffixes;
splits compound commands, pipes, and newlines; follows known shell/wrapper
payloads; decodes PowerShell `EncodedCommand`; and inspects literal Python `-c`
calls such as `os.system`, `subprocess.*`, and `shutil.rmtree`. Inspection is
bounded to eight wrapper levels and 64 command nodes. Ambiguous control flow or
dynamic executable expansion that cannot be classified safely is denied.

Non-disableable circuit breakers reject:

- recursive/destructive deletion of a filesystem root or the workspace root;
- host shutdown/reboot and privilege elevation;
- disk formatting/partition commands and raw block-device writes;
- a recognized shell fork bomb;
- unsafe or over-complex wrapper forms.

This is deliberately accident prevention, not a proof that arbitrary hostile
shell text is safe. A command may reference another absolute path and still
reach the normal approval decision; Full access may then allow it. Use OS
containment when untrusted code requires stronger isolation.

Before launch, variables whose names end in `_API_KEY`, `_TOKEN`, `_SECRET`, or
`PASSWORD` are removed from the child environment. Output is redacted again
before it reaches the model or TUI.

The runner bounds spawn, process wait, process-tree termination, graceful/force
kill waits, Windows `taskkill`, and stdout/stderr drain. The semantic command
deadline is `timeout_seconds`; the Tool Executor's total outer deadline is that
value plus a ten-second cleanup budget. Thus a valid 45-second command is not
cut off by the ordinary 30-second tool limit. The inner timeout reports
`timeout` with execution metadata; the outer deadline is a last resort for a
backend that fails to honor its contract.

An execute observation is recorded immediately before runner startup. Argument
errors, policy hard-denial, and permission denial produce no observation;
spawn/backend failures, timeout, and cancellation conservatively record that an
irreversible attempt may have begun. Each call still produces at most one
terminal tool event and one ToolActivity.

## Skill support tools

These read-only tools are always registered so the model can progressively load
instructions. Skill mode controls eager/named context, not tool registration.

| Tool | Arguments | Result |
| --- | --- | --- |
| `load_skill` | `name`: lowercase hyphenated name, at most 64 characters | Bounded Skill body plus source, truncation, and descriptive `allowed_tools` metadata |
| `read_skill_resource` | `name`; `relative_path` of 1–2,000 characters | One bounded text resource, at most 5,000 estimated tokens |

Workspace Skill packages receive strict anti-link and identity checks on every
load. Details are in [Skills](../extensions/skills.md).

## Local Memory tools

These appear only when `memory.local_file_memory` is true. Their custom
`memory.read`/`memory.write` capabilities defer to the Memory policy; they do
not use the three workspace permission rows and do not prompt. Mutating tool
descriptions tell the model to call them only for an explicit current-user
request, but runtime does not semantically classify the conversation to prove
that request. Runtime enforcement instead requires the matching trusted
workspace, Agent origin, an active Turn, valid content, and the last observed
compare-and-swap hash.

| Tool | Arguments |
| --- | --- |
| `memory_list` | `scope`: `user` or `workspace` |
| `memory_add` | `scope`; `content` (1–2,000 characters); `expected_hash` (64 lowercase hex) |
| `memory_replace` | add fields plus `entry_id` matching `memory_` + 32 lowercase hex |
| `memory_remove` | `scope`; `entry_id`; `expected_hash` |

The hash is compare-and-swap state. On `memory_conflict`, list again and make a
new decision; automatic blind retry could overwrite a concurrent edit. See
[Memory](../extensions/memory.md).
