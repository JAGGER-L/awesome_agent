# Troubleshooting

This page starts from visible symptoms and leads to the smallest safe recovery.
Do not delete state, bypass checksum checks, or raise permissions until the
diagnostic identifies that action as relevant.

## First: Capture the Stable Facts

When the TUI is available, collect:

```text
/status
/config
/doctor
```

Also record `awesome --version`, host/architecture, Workspace path, the exact
command or interaction that failed, and any displayed diagnostic code. For a
Tool failure, expand its bounded detail with Ctrl+O. Redact project-private
paths and outputs before sharing them; never include API keys or secret values.
When Core has started, use the approximate `timestamp` and `operation` to find
its bounded local record in `<AWESOME_HOME>/logs/application.jsonl`. Include
that line's `correlation_id` when sharing the record.

Use this decision path:

```text
cannot launch? --------> installation/runtime
launch stops early? ---> trust/config/state/protocol
Turn cannot start? ----> model/credential/busy interaction
Tool cannot act? ------> argument/path/permission/hard denial
action stopped? -------> cancellation/timeout/uncertain outcome
result cannot restore?> ChangeSet conflict or irreversibility
```

## Installation and Launch

### `awesome` is not found

Open a new terminal after installation. On macOS or WSL2, confirm
`~/.local/bin` is on `PATH`. On Windows, confirm
`%LOCALAPPDATA%\Programs\Awesome\bin` is on the user `PATH`. Close every
Awesome process and rerun the supported installer if the launcher is absent.

Do not install a random package with a similar name from a language package
manager. The supported release contains a matched TUI, Core, private runtimes,
and protocol version.

### The installer rejects the host

Released installers support Windows 11 x64, Apple Silicon macOS, and WSL2
Ubuntu 24.04 x64. Confirm architecture and, for WSL, both WSL2 and Ubuntu
version. The installer fails closed on other hosts because verified runtime
artifacts are part of the release contract. See [Installation](../getting-started/installation.md).

### Download or checksum validation fails

Do not bypass the checksum. Confirm access to GitHub release assets,
releases.astral.sh, and nodejs.org; check whether a corporate proxy rewrites
downloads; then retry. A persistent mismatch can be a damaged or incomplete
release and should be reported with the asset name and exact error.

### Another installer is running or an upgrade was interrupted

One exclusive installer lock covers recovery, download, same-root staging,
application replacement, launcher replacement, and post-commit cleanup. Wait
for a live installer rather than starting another. If it was terminated, rerun
the same version-bound installer; it validates the recorded owner before
reclaiming a dead lock and reconciles `.install-transaction` with
`app.rollback`. Do not delete those paths manually unless you have separately
proved that no installer process is alive.

If the run ends with a `PATH`, shell-profile, or deferred-cleanup warning, the
application may already be committed. Open a new terminal or add the documented
launcher directory manually, check `awesome --version`, close running Awesome
sessions, and rerun once to clean rollback residue. Do not interpret a cleanup
warning as permission to overwrite release assets or bypass checksums.

### Interactive Awesome requires a TTY

The Ink UI requires TTY input and output. Start `awesome`, `--continue`, and
`--resume` directly in a terminal, not through a non-interactive pipe or
redirected standard output. For automation, use the dedicated headless path:

```text
awesome run "<prompt>" [--new | --thread <id>] [--format text|json]
```

`awesome run` deliberately skips only the TTY requirement. It still starts the
same private Core, performs the same Application startup and trust checks, and
uses normal Thread/Turn persistence and permission policy.

### `awesome run` exits without stdout

This is intentional for every nonzero exit; read stderr and inspect the exit
code. Code 1 is a run failure, code 2 is an invocation/runtime failure, code 3
means an interaction such as trust, state reset, Thread selection, or approval
was not resolved, and 130 means SIGINT requested cancellation. Add
`--trust-workspace` only after verifying the current directory. Select an
appropriate `--permission-mode` only when its consequences are acceptable.
The runner never emits a partial final answer on failure or interruption.

### Core cannot start or the protocol/version handshake fails

Close all Awesome processes and rerun the original one-line installer. Mixing
a TUI from one release with a Core from another is rejected explicitly; a
shared product version string is not a substitute for the private protocol
version. Reinstallation replaces the application only after the staged release
passes its own version checks.

## Workspace Startup

### The wrong Workspace is shown

Exit, change to the intended existing project directory, and start `awesome`
again. The current directory is the Workspace; there is no public launch flag
for changing it in an installed release. Verify the displayed startup path in
the trust prompt before accepting. Core canonicalizes it internally.

### Workspace trust keeps appearing

Choosing No intentionally saves no denial. If you previously chose Yes, either
the canonical target changed or local trust state was reset; aliases that
resolve to the same canonical directory share a trust record. Replacing the
physical root while a session is active instead invalidates that session's
identity checks. Confirm the displayed path and target rather than trusting a
replacement solely because its text looks familiar.

### Startup says the Workspace is already in use

Another Awesome process may hold the canonical-path or filesystem-identity
lease, including through an alias. Close the other session and retry. Do not
copy or remove lock/state files while an owner may still be alive.

### `AGENTS.md` was ignored

Read the full reason in Welcome, the status line, or `/doctor`. The root file
must be a stable plain UTF-8 file with no NUL bytes, links, junctions, reparse
components, or replacement during the read. It must fit the 32 KiB byte limit
and the smaller of 8,192 Tokens or 10% of the effective input budget.

Awesome ignores an invalid file whole. Fix it and start a new Session; the
current Session keeps its immutable snapshot. A missing `AGENTS.md` is normal
and produces no warning.

## Configuration and Models

### Configuration is invalid

Inspect `<AWESOME_HOME>/config.yaml` and, after trust,
`<workspace>/.awesome/config.yaml`. User configuration uses `version: 2` and
Workspace configuration uses `version: 1`; user version 1 remains readable and
is upgraded on the next supported write. Duplicate keys, unknown keys, unsupported model IDs, invalid
names, and out-of-range budgets are errors.

Temporarily reducing User configuration to `version: 2`, or Workspace
configuration to `version: 1`, can isolate which optional section is invalid,
but preserve a backup outside the repository before manual
editing. Do not move credentials into Workspace configuration. Restart Awesome
after a manual fix and run `/config`.

### No model is configured

Press Enter on the setup notice or run `/model`. Choose DeepSeek or Kimi, enter
the key through masked input, and select a model. If both Providers are absent
and no model default is valid, a Turn cannot start because Core refuses to
invent a Provider.

### A credential is shown as Unavailable

Run `/auth <service>`. The selected Environment and Awesome-managed sources
are independent. Restore the selected source or explicitly choose the other
one. Awesome does not silently fall back when a selected source disappears.

Environment values are captured from the process that launched Awesome; change
the parent shell and restart. Awesome-managed values can be added, replaced, or
deleted in `/auth`.

### A key is invalid or unverified

A known-invalid DeepSeek or Kimi key is not saved. Confirm the Provider,
region, key status, and network. A network/Provider failure offers an explicit
Save anyway path and marks the result unverified; this does not prove the key
works. Run `/doctor` later for an on-demand validation.

Deleting a local key does not revoke it at the Provider. Revoke compromised
credentials in the Provider's own console.

### The effective budget is lower than configured

The selected model context limit can cap the total context. A trusted Workspace
can also lower, but never raise, user Turn budgets. Use `/context` for the
effective input limit and `/usage` for consumption; compare both user and
Workspace configuration.

## Busy, Pending, and Cancellation

### Input is shown as pending

Awesome runs one foreground Operation at a time. The TUI queues up to three
later messages, slash commands, or direct commands in submission order. Wait,
press Ctrl+C to cancel the active Operation, or use Up with an empty Composer
to recall the newest pending item into the draft.

A full queue or queued `/quit` leaves additional text in the Composer and
explains why it was not accepted. The queue is session-only and is not restored
after exit.

### `operation_busy`

A Turn, direct command, state-changing command, interaction resolution, or
shutdown already owns the mutable foreground. Do not repeatedly submit the
same mutation. Wait or cancel the visible owner. Core permits a small snapshot
allowlist during a normal Operation, but the current TUI queues all later input,
including `/status` and `/usage`, so their results appear only after the active
Operation finishes.

### `interaction_busy`

A Trust, Approval, Full access, state-reset, or recovery prompt is unresolved.
Return to that prompt and accept or deny it. Starting a new Thread or changing
permissions is intentionally blocked until the decision is closed.

### Ctrl+C does not stop immediately

Cancellation includes bounded handler and process-tree cleanup. Wait for the
terminal cancelled or failed event before starting replacement work. A command
or MCP service may have acted already; cancellation cannot retract an external
side effect.

## Tools and Shell

### A file path is rejected

Built-in tools require a safe Workspace-relative path. Check for an absolute
path, `..` escape, symlink/junction/reparse component, sensitive secret/key
path, multiple hard links, ambiguous Windows syntax, or a target that changed
during inspection. Move the intended work into a normal Workspace path rather
than asking for Full access; permission mode does not disable path safety.

### A delete fails before changing anything

Recursive delete inventories the entire tree first and rejects any nested
symlink, junction, or reparse directory. Remove the alias manually only after
you verify its target, or delete ordinary child paths individually. The
preflight failure is designed to leave both Workspace and external targets
unchanged.

### A command is hard denied

Simplify wrappers, substitutions, or compound syntax into explicit inspectable
commands. Hard denials remain active in all permission modes and for `!`
commands. Full access cannot override recognized privilege elevation,
shutdown, disk, block-device, fork-bomb, or root/Workspace-root recursive
deletion rules.

If a safe command genuinely cannot be expressed in an inspectable form, run it
yourself in an appropriately isolated shell. Do not encode or obscure it to
evade the circuit breaker.

### A command times out

Expand the Tool detail and inspect exit/timeout/truncation metadata. Core
attempts bounded process-tree cleanup, but a daemonized child or external side
effect may remain. Inspect the Workspace, process list, and external target
before retrying. A longer timeout is appropriate only when the command is known
to make progress and duplicate execution is safe.

### Output is truncated or redacted

Tool output is bounded to protect the terminal and context. A descendant that
keeps a pipe open can also cause drain truncation. Redirect large non-secret
artifacts to a reviewed Workspace file when appropriate, then read the relevant
portion. `[REDACTED]` indicates a safety pattern matched; it does not guarantee
that every secret form was detected.

## Changes and Recovery

### `/diff` is empty after a shell command

The Change Journal snapshots built-in file mutations. Shell execution records a
conservative attempt but does not infer arbitrary filesystem deltas. Inspect
the command's own output and normal project tools such as version-control
status.

### `/undo` reports `workspace_conflict`

At least one current path no longer matches the ChangeSet's expected applied
state. No path is restored. Preserve the current files, compare `/diff`, and
integrate the desired reversal in a new Turn. Do not overwrite later user or
process changes to make the old undo pass.

### A ChangeSet is not reversible

An execute-only ChangeSet has no captured file before/after state. A mixed set
restores only built-in file changes and warns that unmanaged execute effects
remain. Verify shell and MCP targets manually.

### Awesome asks whether to recover an unfinished Turn

For a verified local checkpoint, Retry is listed first and continues from the
frozen context. When a file mutation, shell, MCP, or Web call may have acted,
Abort is listed first; Retry may repeat remaining side effects. Inspect affected
files and external targets before deciding. Awesome never transparently replays
an uncertain call.

### Awesome asks to reset local state

Review the confirmation panel. **Reset local state and continue** removes local
conversations, Workspace trust, checkpoints, and undo history. It preserves
API keys, configuration, Skills, and Local or Cloud Memory settings. Choose
Exit or press Escape to leave state unchanged.

If another Session uses the state, close it and retry. State created by a newer
Awesome version requires an upgrade and is never reset by an older binary.
Unknown, corrupt, unreadable, or locked state produces a diagnostic instead of
a destructive prompt. Do not delete the data directory manually.

## Extensions

### Web Search or Fetch is unavailable or fails

Run `/web status`. `enabled: false` means run `/web on`; a missing credential
requires `/auth tavily` to select an available Environment or Awesome-managed
key. `web_proxy_invalid` means the explicit
`AWESOME_WEB_PROXY_URL` is not a bounded credential-free `http` or `https` URL.
Ambient `HTTP_PROXY`/`HTTPS_PROXY` settings are intentionally ignored because
the Web client uses `trust_env=False`.

For `web_fetch`, `invalid_arguments` means the requested value is not one public
absolute HTTPS URL, names a private/special-use host, includes unsupported user
information/fragment syntax, or appears to target a PDF or another recognized
binary format. `web_request_rejected` means Web configuration blocks the host
or Tavily rejected an otherwise admitted request. Tavily's cloud service, not
Awesome Core, connects to an admitted target.

If the tool asks despite Full access, that is expected: `network.read` asks on
first use in every permission mode. Choose once or current Thread, or use
`--allow-network` for one exact headless Turn. `web_request_budget_exhausted`
means Search and Fetch have consumed the current Turn's shared request budget;
it is distinct from provider-account `web_quota_exhausted`. `web_rate_limited`,
`web_quota_exhausted`, `web_timeout`, `web_connection_failed`, and
`web_provider_unavailable` are stable provider-boundary failures; Awesome does
not transparently retry them. `web_malformed_response` means the bounded Tavily
response did not satisfy the strict contract. Queries and result URLs are
intentionally absent from structured logs, as are requested Fetch URLs and
extracted content.

### A Skill is missing or invalid

Run `/skills` and inspect the source and diagnostic. Check the package name,
disabled lists, bounded UTF-8 `SKILL.md`, and for Workspace Skills every
`.awesome/skills/<package>` path component and resource. Restart after fixing a
discovery-time problem. One bad package should not hide other valid Skills.

### Local Memory is disabled, invalid, or conflicts

Run `/memory`, then inspect the requested user or Workspace scope. Memory is
off by default and explicit mutations use content hashes to prevent lost
updates. Resolve the concurrent edit and retry against a fresh list rather
than replacing the file blindly.

If enabled `USER.md` or `MEMORY.md` is invalid or unreadable, Turn context
preparation fails instead of omitting the scope. The same Operation terminalizes
the already-created Turn as failed and attempts to remove its checkpoint, so
another Turn can start normally. If cleanup fails, the primary error is
preserved and startup reconciliation retries removal of the residual
checkpoint. Repair the managed markers/UTF-8/size problem or disable local
Memory before retrying.

### Mem0 Cloud is unavailable

Confirm `/auth mem0`, selected credential availability, and `/memory mem0 on`.
Network or SDK failure degrades cloud Memory without disabling the local Agent
loop. The exact diagnostic distinguishes initialization, search, write, and
delete failures.

### MCP is disconnected or in error

Run:

```text
/mcp
/mcp status <id>
```

Confirm the configured command, arguments, allowlisted environment variable
names, executable availability, and server schema. `/mcp restart <id>` removes
the old namespace before reconnecting. `connected` means the live client,
complete compiled catalog, and complete Registry namespace were all published.
If publication reports `error`, inspect the server catalog for an invalid
schema or final `mcp.<server>.<tool>` name longer than 128 characters. Also
reduce the enabled tool set or schema definitions if the effective shared
Registry would exceed 128 tools or 1 MiB; a rejected candidate leaves no stale
namespace and does not disturb another server's namespace.

After an MCP timeout or connection loss, the server may already have acted.
Awesome invalidates the catalog and reports a non-retryable uncertain outcome
instead of reconnecting and replaying inside the same Turn.

## Application invocation diagnostics

Awesome writes local structured invocation records to
`<AWESOME_HOME>/logs/application.jsonl`, with `.1` through `.4` as older rotated
files. Each file is capped at 5 MiB. Locate a line by its approximate timestamp
and `operation`, then inspect its `outcome`, duration, and optional stable
`error_code`. The `correlation_id` identifies that diagnostic line when
reporting it. Do not expect request arguments, prompt text, model or Tool
bodies, queries, URLs, paths, or secrets to appear.

Logging is deliberately nonblocking and fail-open. A missing line can mean the
bounded queue was full or local file I/O failed; it does not mean the
Application invocation failed. Conversely, a successful invocation record may
only mean that Agent work was accepted. Use terminal Turn events and
Conversation state to determine the later asynchronous Turn outcome.

## Reporting a Reproducible Problem

Include:

- Awesome version and supported host/architecture;
- whether the failure occurs before or after Workspace trust;
- exact diagnostic code and sanitized message;
- the smallest command or request that reproduces it;
- relevant permission mode and extension status;
- whether cancellation, timeout, or an external side effect occurred;
- whether the problem reproduces in a new Thread and new Session.

Do not include API keys, secret files, raw private prompts, full tool output, or
the entire state database. For deeper behavior, consult
[Core Concepts](../concepts/README.md) and the [Reference](../reference/README.md).
