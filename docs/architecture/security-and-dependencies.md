# Security and dependencies

Awesome is a trusted-workspace local coding agent. It deliberately runs tools
on the host to participate in normal developer workflows. It is not a security
sandbox, container, virtual machine, or boundary against a malicious
same-privilege process.

Security claims must begin with that limit. Workspace trust, permission prompts,
path validation, process cleanup, and a command circuit breaker reduce specific
risks; none turns host execution into isolation.

## Threat model

Awesome is designed to resist or fail safely for:

- accidental activation of project-controlled instructions before trust;
- replacement, alias, link, and reparse attacks on identity-pinned workspace
  roots, filesystem tools, `.awesome/config.yaml`, `AGENTS.md`, and Workspace
  Skills;
- common structured-filesystem path escapes and sensitive-file requests;
- known catastrophic shell commands and wrappers;
- stale approvals crossing Threads, Turns, Operations, or permission modes;
- malformed provider, MCP, Skill, protocol, or persisted input;
- unbounded tool/model output, MCP schemas/catalogs, context, process cleanup,
  or request concurrency;
- ordinary process crashes during Turn and managed file mutation commits;
- accidental secret rendering in supported events and tool output.

The current design does not claim to resist:

- a malicious user or process with the same OS account replacing local state;
- arbitrary hostile shell obfuscation;
- a command intentionally escaping a POSIX process group;
- filesystem races requiring kernel compare-and-swap or a mount boundary;
- exfiltration by a successfully approved host process;
- compromise of the operating system, terminal, provider, MCP server, or
  dependency distribution channel;
- rollback of every external shell, MCP, network, or service side effect;
- power-loss atomicity across SQLite, blob directories, and workspace files.

If the required threat model includes untrusted code execution, use an external
OS/container sandbox and do not treat Awesome's Full access mode as equivalent.

Security inputs include workspace identity/content, tool arguments, shell text,
approval decisions, extension catalogs/results, provider output, local state,
and protocol frames. Security outputs are allow/ask/deny decisions, bounded
facts and diagnostics, owned process cleanup, and conservative recovery
evidence. No control outputs a claim that allowed host code is isolated.

## Layered decision model

Four distinct questions are evaluated in order:

```text
1. Workspace trust: may project-controlled content influence this session?
2. Hard safety: is this known operation forbidden even with user approval?
3. Permission: may this capability run now, or must the user decide?
4. Execution isolation: what can an allowed process access at the OS layer?
```

Awesome implements the first three. It does not currently provide the fourth.
Keeping the layers distinct avoids telling users that clicking an approval
created a sandbox.

This distinction follows the same general layering documented by
[OpenAI](https://learn.chatgpt.com/docs/sandboxing#how-permissions-work),
[Claude Code](https://code.claude.com/docs/en/permissions), and
[Hermes](https://hermes-agent.nousresearch.com/docs/user-guide/security/),
while Awesome's exact behavior is defined only by this repository.

## Trust before project influence

Startup canonicalizes and identifies the requested workspace. Until trust is
accepted, Core does not read workspace configuration, root `AGENTS.md`,
workspace Skills, MCP declarations, or run tools. Only acceptance is durable;
denial exits without persisting a negative policy.

Activation acquires both path-key and physical-entity leases. It then rechecks
the opened root identity. A pathname replacement or alternate alias cannot
create a second live session that treats the first session's Turn as crashed.

Persistent trust is keyed by the canonical workspace path. Physical identity
and the path/entity leases are live-session guards, not part of the durable
trust record. Neither form of binding grants authority to arbitrary content
later reached through a link.

After trust, `.awesome/config.yaml` uses the same Core no-follow reader boundary
as workspace instructions and Skills. It accepts one plain UTF-8 file up to
1 MiB, rejects NUL, links/reparse points, hard links, and non-regular nodes, and
pins and rechecks the opened directory and file identities. A replacement or
oversized document fails configuration activation instead of redirecting or
truncating project-controlled input.

## Workspace instructions

Root `AGENTS.md` is loaded only after trust and only once per session. The read
uses `lstat`, bounded open, and post-open identity verification. It rejects
links/reparse points, replacement during read, path escape, NUL, non-UTF-8, more
than 32 KiB, or more than the smaller of 8,192 tokens and 10% of effective
input capacity.

Oversized content is ignored whole rather than truncated into potentially
different rules. A structured diagnostic remains visible in Welcome, the
status line, and `/doctor`, but does not make otherwise valid configuration
invalid.

## Authorization and stale-decision defense

The three permission modes govern known built-in capabilities as documented in
[Tools and changes](tools-and-changes.md). MCP and unknown capabilities always
ask once, including in Full access.

Approval is bound to the authority that requested it:

- Tool approval: Thread + Turn + Operation + interaction generation;
- Full access confirmation: selected Thread + permission generation;
- recovery decision: Thread + Turn;
- startup trust/reset: bootstrap state and interaction identity.

Mode or Thread changes invalidate stale Full access confirmation and clear
temporary grants. Full access confirmation selects the safe “keep current
mode” choice by default. Tool approval is a continuation of its owning
Operation; other resolutions acquire the foreground resolving lease before
revalidating and changing state.

The TUI presents Core's operation and target. It cannot manufacture a grant by
changing display text, and it never executes an approved call itself.

## Filesystem boundary

Structured filesystem-tool policy rejects absolute and escaping paths,
link/reparse traversal, sensitive credential/key names, and ambiguous Windows
syntax. Bounded readers verify the opened object's identity, type, size, link
count, and modification time after reading. Mutations use pinned directory
chains and no-follow primitives where available. Deleting a final link node
without following its target is distinct from traversing it and is supported on
the safe platform paths covered by tests.

Recursive delete inventories the full tree before deleting anything. A nested
symlink, junction, reparse directory, hard-linked file, capacity violation, or
identity mismatch aborts the inventory, leaving intended workspace and external
targets unchanged.

These mechanisms prevent filesystem tools from following a project-controlled
link outside the workspace. They cannot stop every same-privilege race after
the final identity check. On POSIX another process can also move an already-open
parent directory; descriptor-relative work still targets that object. A
stronger guarantee needs a kernel-supported exclusive writer, mount namespace,
or sandbox.

## Shell boundary

The pure command policy receives the command, explicit CMD/POSIX/PowerShell
dialect, working-directory candidate, and workspace. Both direct and Agent
paths first evaluate the requested lexical directory at executor admission; for
Agent calls this occurs before any approval, while Direct input is already the
user's explicit authority. The handler then resolves and identity-checks that
directory and calls the same policy with the verified resolved path immediately
before spawn. The first stage limits what can be proposed; the second prevents
execution when real path evidence is unsafe.

Known wrappers, compound commands, pipelines, newlines, executable aliases,
PowerShell encoded commands, and selected literal Python calls are inspected
within strict depth/node limits. Known catastrophic deletion, shutdown,
elevation, formatting, device overwrite, and fork-bomb patterns are denied in
all modes. Unsafe parse uncertainty is a denial.

This is a non-disableable accident circuit breaker, not a complete shell parser
or hostile-code classifier. Once allowed, a command executes with the sanitized
host environment and host account privileges. Variables whose names end in
common API-key/token/secret/password suffixes are removed from the execute
environment, but this is not a proof that no sensitive host data is reachable.

The shell boundary is intentionally wider than the filesystem-tool boundary.
It can name sensitive or outside-workspace paths with the Awesome host account;
environment-variable scrubbing does not isolate files. The handler validates
the working-directory identity and reruns policy, but the runner receives its
pathname rather than a pinned directory descriptor. A same-privilege process
can still replace that directory between validation and OS spawn.

## Process lifetime

Core establishes process-tree ownership before async product startup. Windows
uses kill-on-close Job Objects; POSIX uses sessions/process groups and lease
supervisors. Each `execute` gets a separate cleanup domain. Timeout,
cancellation, spawn failure, root completion, termination, force-kill, and
stdout/stderr drain all have bounds.

Process ownership addresses orphan cleanup. It does not limit filesystem,
network, device, credential-store, or child-process access while the command is
running. A deliberately daemonized POSIX process can leave the owned group.

## Extension and context boundary

Workspace instructions, Skills, Memory, MCP descriptions/results, explicit
files, and provider text are untrusted input to the model. They cannot change
the capability decision or bypass the Tool Executor.

Workspace Skill discovery/load revalidates every path identity without
following links. Under one server lock, the MCP Manager compiles one whole
validated, bounded, local-reference-only candidate, including the final
namespaced names. The shared Registry first validates its complete aggregate
snapshot (128 tools and 1 MiB of canonical model definitions) and atomically
replaces that server namespace. With no intervening `await`, the Manager then
publishes the generation, client, catalog, and `CONNECTED`; connected therefore
also proves namespace installation. Failure closes the candidate client,
invalidates the generation, removes that namespace, and exposes only a fixed,
sanitized diagnostic. Arguments are schema-validated before approval and remote
I/O. Generation-bound handlers cannot call a newer catalog through an old
validator. Timeout or connection loss is an uncertain outcome and is not
replayed transparently. Every MCP invocation still requires one-call approval,
including in Full access.

One invalid Skill or MCP server is isolated as a diagnostic; it does not widen
another extension's permissions.

## Secrets and sensitive data

Provider secrets come from the process environment or the user-owned `.env`
managed by explicit `/auth` choices. Workspace configuration cannot supply
secret values. If the selected source disappears, Awesome reports it and does
not silently fall back to another source.

Secret values are represented with secret-aware types at configuration
boundaries and redacted from supported event/output paths. Tool audit records
argument names rather than raw values. Direct command/output persistence runs
through redaction and length bounds.

Redaction is defense in depth, not data-loss prevention. An approved shell
command can read host data available to the user, and a prompt can ask a model
to reveal content it was intentionally given. Avoid placing secrets in the
workspace or passing them as tool arguments.

## Dependency direction

Package dependencies encode authority, but the actual import contract is not a
single vertical DAG. Storage implements several lower-owned ports, Extensions
uses Context contracts, Safety uses provider-neutral Modeling types, and
Application is the concrete composition root. The exact importer-to-allowed
adjacency table is maintained in the root
[architecture overview](../../ARCHITECTURE.md#file-dependency-chain) and
enforced by `tests/structural/test_dependency_architecture.py`; conceptual data
flow diagrams must not be interpreted as import permission.

Important framework ownership is also fixed:

| External framework | Allowed owner |
| --- | --- |
| `jsonschema` | Extensions/MCP |
| `mcp` SDK | Extensions |
| `openai` SDK | concrete Providers |
| `sqlite3` | Storage |
| `langgraph` | Agent, Application invocation, and Storage checkpoint adapter |

Framework ownership prevents a convenience import from creating a hidden
second provider, database, schema validator, or graph runtime.

## Production dependency rationale

Awesome keeps a small explicit production set:

| Dependency | Owned use |
| --- | --- |
| `pydantic` | typed cross-boundary models and validation; strictness is contract-specific |
| `langgraph`, `langgraph-checkpoint-sqlite` | one Agent graph and its checkpoint saver |
| `openai` | DeepSeek/Kimi-compatible provider clients behind adapters |
| `mcp` | stdio MCP client behind Extensions |
| `jsonschema` | standards-compliant MCP schema compilation |
| `PyYAML` | YAML parsing, duplicate-key rejection, configuration, and Skill frontmatter |
| `python-dotenv` | user secret source loading |
| optional `mem0ai` | explicitly enabled Mem0 Cloud adapter |

Adding a dependency must identify its owning package, why an existing contract
cannot satisfy the need, how input/output is bounded, and which supply-chain
and packaging gates cover it. `tests/structural/test_product_architecture.py`
locks the direct dependency inventory.

## Supply-chain controls

The Python and npm lockfiles are committed. Required CI checks locked installs,
wheel/package contents, Protocol fixtures, and structural ownership. The
separate `Security required` workflow runs dependency review, CodeQL, pip-audit
through its hash-validating PyPI path plus OSV lookup, and npm audit. GitHub
Actions are pinned to full commit hashes and Required CI validates workflow
syntax with actionlint.

GitHub's Dependency Graph, Dependabot, rulesets, tag protection, release
environment reviewers, secret scanning, and push protection are repository
settings, not facts that source code alone can enforce. Maintainers must verify
those controls separately.

## Security change checklist

For any new tool, provider, extension, storage format, or process path:

1. State the trusted and attacker-controlled inputs.
2. Identify the capability and whether hard denial applies.
3. Validate and bound input before approval or external I/O.
4. Bind approvals to current execution identities.
5. Define timeout, cancellation, uncertain-outcome, and cleanup behavior.
6. Decide what is durable and how recovery distinguishes committed from
   uncertain work.
7. Prove the lower package does not import a higher authority.
8. Add normal, malformed, boundary, race, cancellation, and recovery tests.
9. Document residual risks without calling policy an isolation boundary.

## Design tradeoffs

- Host execution integrates with native developer tools but leaves isolation to
  an external sandbox.
- Strict workspace link/hard-link rejection excludes some legitimate layouts
  in exchange for explainable containment.
- Fail-closed parser and schema bounds can reject complex valid inputs; accepting
  unbounded or ambiguous work would make policy timing and meaning uncertain.
- A small direct dependency set concentrates ownership but requires explicit
  product work for each new provider or extension framework.
- Conservative recovery may preserve unresolved evidence and require user
  action; automatic cleanup could erase proof of an external or file effect.

## Source and test map

- Trust and identity: `core/workspace/`, `storage/trust.py`,
  `application/composition.py`
- Filesystem: `core/filesystem.py`, `core/tools/policy.py`
- Command policy/process: `core/tools/command_policy.py`,
  `core/tools/process.py`, `core/process_lifetime.py`
- Permissions/interactions: `core/tools/permissions.py`,
  `application/interactions.py`
- Redaction: `safety/redaction.py`
- Dependency tests: `tests/structural/test_dependency_architecture.py`,
  `tests/structural/test_product_architecture.py`
- Security-sensitive tests: `tests/unit/core/`, `tests/unit/extensions/`,
  `tests/integration/test_workspace_trust.py`,
  `tests/integration/test_state_reset_concurrency.py`
