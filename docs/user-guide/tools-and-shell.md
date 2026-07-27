# Tools and Shell Execution

This page is for users who want to understand what happens between a model tool
request and a local effect. It covers built-in tools, validation, approvals,
shell behavior, timeouts, output, and audit boundaries.

## One Execution Path

The model never calls the filesystem, shell, Memory, or MCP directly. Every
tool name resolves through one registry and one executor:

```text
model or direct request
        |
        v
 registry lookup -> argument validation -> path/command hard checks
        |
        v
 capability policy -> optional approval -> bounded handler
        |
        v
 terminal event + ToolActivity + optional ChangeSet
```

This shared path is why an extension cannot bypass approval simply by exposing
a new name. `/tools` displays the effective registry, read-only status, and
whether each tool currently requires approval.

## Built-in Tool Families

Awesome initially registers:

- discovery: `ls`, `glob`, and `grep`;
- reading: `read_file`;
- writing: `write_file` and `edit_file`;
- deletion: `delete`;
- host execution: `execute`.

When Web is enabled and configured, the same registry also contains
`web_search`. It is provider-neutral to the model, passes the same strict
validation/policy/approval/audit path, and is marked non-replayable.

The total catalog can grow through Memory and MCP. Namespaced extension tools
still use the executor. Exact argument schemas, bounds, and result fields are
in [Built-in tools](../reference/built-in-tools.md).

### Using Web search

Set `TAVILY_API_KEY`, run `/web on`, then ask for current information. The first
`network.read` call asks even in Full access; choose deny (the default), allow
once, or allow for this Thread. `/web status` shows readiness and disclosure,
`/web revoke` clears the active Thread grant, and `/web off` removes the tool
from the rebuilt runtime.

Awesome sends the query to Tavily under its
[Privacy Policy](https://www.tavily.com/privacy) and
[Platform Terms](https://www.tavily.com/terms). It assigns `S1...` citations
to strict HTTPS results and preserves them in the final answer, transcript,
headless JSON, and checkpoint. The default limit is eight requests per Turn.
See the [exact tool contract](../reference/built-in-tools.md#public-web-search-web_search).

## Model-Driven Versus Direct Shell

Ask in natural language when the model should choose the command:

```text
Run the smallest test that covers the parser change.
```

Use direct execution when you have already chosen the exact command:

```text
! git diff --stat
```

Both paths use the same `execute` handler, command policy, sanitized
environment, process runner, timeout, output bounds, redaction, and journal
observation. Direct execution supplies explicit user authority and therefore
does not show a normal shell approval prompt. It does not bypass hard denials.

## Workspace Paths

Built-in file tools accept Workspace-relative paths, not arbitrary absolute
paths. Core validates platform-specific syntax before it follows any component,
then binds the Workspace root, parent directories, and target identity with
no-follow operations.

The checks reject Workspace escapes, links or reparse traversal, sensitive
secret/key paths, ambiguous Windows spellings, and regular files with multiple
hard links. Reads are bounded and rechecked for stable content and metadata.
Writes use an atomic sibling replacement where supported. Recursive delete
builds and validates its complete inventory before deleting anything and
refuses nested symlink, junction, or reparse directories.

On supported POSIX paths, deleting the final symlink node itself is allowed
because Core removes the link without following its target. A linked parent,
nested link, or Windows directory reparse target remains rejected.

These controls prevent built-in path resolution from following an alias to an
external target. They do not prevent another same-privilege host process from
racing ordinary filesystem operations in every portable edge case. Use an OS
isolation or mount boundary against a hostile concurrent process.

## Command Inspection

Before approval, Core evaluates the command text, host shell dialect, Workspace,
and the requested lexical working directory under the canonical Workspace
root. The handler then verifies that the directory exists, remains inside the
Workspace, is not a link, and has a stable identity. Immediately before process
start, the same pure policy runs again using that verified resolved directory.
The evaluator is shared, but the two stages intentionally have different path
evidence; the second check prevents the runner from starting if resolution or
policy is unsafe.

The final OS spawn still receives a working-directory pathname rather than a
pinned directory handle. A same-privilege process can replace it after the
second check and before spawn. This is one reason host execution remains outside
the filesystem tools' stronger identity-pinned boundary.

The bounded parser handles known CMD, POSIX-shell, and PowerShell wrappers;
compound commands, pipelines, and newlines; directory-changing segments;
encoded PowerShell; elevation aliases; and selected literal Python `-c`
process/filesystem calls. It normalizes executable paths, case, and common
Windows executable suffixes. Unsafe or unparseable forms are denied.

The parser is a circuit breaker for recognizable accidents, not a proof of
program behavior. For example, a benign `python -c "print('rm -rf /')"` can be
distinguished from a literal destructive call, but an arbitrary downloaded
program cannot be understood from its filename.

## Working Directory and Environment

The `execute` tool defaults to the Workspace root and may select an existing
Workspace-relative directory. A symlink working directory or path outside the
Workspace is rejected.

The child environment is inherited after removing variable names ending in
common secret suffixes such as `_API_KEY`, `_TOKEN`, `_SECRET`, or `PASSWORD`.
This reduces accidental credential exposure; it is not a complete secret
scanner and does not revoke credentials available through files, agents, or
OS stores.

## Timeouts, Cancellation, and Cleanup

An `execute` request chooses a positive command timeout of at most 600 seconds,
with 60 seconds as the default. Core gives the tool an additional bounded cleanup
budget for process-tree termination and output-pipe draining. Other tools use
the normal outer tool deadline.

On timeout, the process runner attempts graceful and then forced tree cleanup.
On Windows each command is supervised with a Job Object; on POSIX it uses a
process group. Descendants that intentionally escape the managed group may
survive. A descendant that inherits an output pipe cannot hold the call open
forever; output may instead be marked truncated.

Ctrl+C preserves cancellation as the terminal outcome after bounded cleanup.
Timeout, cancellation, spawn failure, and backend failure each produce one
terminal Tool event and one ToolActivity. Since a command may have acted before
failure was observed, the Change Journal records the redacted execute attempt
before the runner starts. Invalid arguments, permission denial, and hard denial
do not record an attempted execution.

## Output and Redaction

Standard output and standard error are bounded separately, redacted, and then
rendered with exit code, duration, timeout, and truncation metadata. A nonzero
exit code is a completed process with a failed presentation; a runner failure
or timeout is a Tool error. Current-session details can be expanded with
Ctrl+O, while resumed Threads retain durable bounded summaries rather than raw
tool streams.

Do not rely on terminal redaction as the only protection for secrets. Avoid
printing them in the first place, and enter Provider credentials only through
the masked `/auth` or `/model` flow.

## Practical Examples

Read and explain without changes:

```text
Inspect @src/parser.py and explain how invalid tokens are reported. Do not edit
files or run commands.
```

Allow ordinary edits but keep commands gated:

```text
/permissions accept_edits
Add a regression test for the empty-token case and implement the smallest fix.
```

Run a reviewed command directly:

```text
! git status --short
```

Review captured file effects:

```text
/diff
```

## When a Tool Fails

Use `/tools` to confirm the name and approval state. A path failure usually
means the target is absolute, outside the Workspace, sensitive, aliased, or
changed during inspection. A command denial means the bounded policy could not
establish an acceptable form; simplify it into explicit inspectable steps.
A timeout means the command outcome may be partial, so inspect the Workspace or
external target before retrying.

See [Troubleshooting](troubleshooting.md) for symptom-based recovery and
[Permissions and safety](permissions.md) for the authority model.
