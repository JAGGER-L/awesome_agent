# Roadmap

This roadmap separates what Awesome does today from product directions that
still require design and implementation. It describes intent and architectural
constraints, not release dates or compatibility promises.

For current behavior, use the [user guide](user-guide/README.md) and
[reference](reference/README.md). A roadmap item must not be documented as an
available command, configuration key, or subsystem until its implementation and
tests land.

## Current foundation

Awesome currently provides:

- one terminal product surface backed by one private Python Core;
- DeepSeek and Kimi through provider-neutral model contracts;
- trusted Workspace startup, root `AGENTS.md` instructions, Threads, Turns,
  cancellation, checkpoints, and recovery;
- workspace file tools and bounded local command execution through one Registry,
  Policy, and Executor path;
- three permission modes, single-use approval, a Thread-bound temporary
  `workspace.write` grant, and a non-disableable command circuit breaker;
- visible ChangeSets with diff, undo, redo, and conservative crash recovery;
- independently optional local Memory and Mem0 Cloud;
- bundled, user, and trusted Workspace Skills;
- configured MCP stdio servers with validated, generation-bound catalogs;
- a versioned Protocol v3 boundary between Python Core and the Ink TUI;
- a searchable GitHub Pages documentation site generated from this directory.

The [architecture overview](../ARCHITECTURE.md) defines the current component
boundaries. The rest of this page discusses possible additions.

## Current known limitations

These are implemented behaviors or contract gaps in the current release, not
future features and not guarantees that the limitation is desirable:

- The trusted-workspace `.awesome/config.yaml` reader is trust-gated but not
  size-bounded, no-follow, or identity-pinned like `AGENTS.md` and Workspace
  Skills. See [configuration](reference/configuration.md#workspace-configuration).
- Most built-in and Skill-support tool argument models ignore unknown fields
  and coerce compatible scalars; configuration and Skill frontmatter also have
  documented coercion/grammar mismatches. See the
  [tool contract](reference/built-in-tools.md#common-request-and-result-contract),
  [configuration](reference/configuration.md), and
  [Skills](extensions/skills.md#create-a-skill).
- Skill modes `auto` and `off` currently have the same observable behavior; no
  automatic selector exists. See [Skill selection](extensions/skills.md#select-and-load-skills).
- `direct.execute` accepts a 30,000-character transport field before delegating
  to an 8,000-character tool field. See the
  [method table](reference/protocol.md#method-catalog).
- `/auth mem0` stores locally valid input without remotely verifying the Mem0
  credential; failure appears on the first cloud operation. See
  [Memory configuration](extensions/memory.md#configuration).

Closing one of these gaps requires runtime regression tests and synchronized
reference/architecture updates. Removing a bullet from this list without that
evidence would turn documentation into an unsupported promise.

## Near-term product directions

### One-command Skills installation

**User need:** finding and installing a Skill should not require manually
constructing package directories.

**Invariant:** installation must preserve manifest validation, source
precedence, Workspace trust, bounded reads, and the existing tool-policy path.
Discovery convenience must not turn a Skill into executable authority.

**Open decisions:** registry ownership, package authenticity, update behavior,
version pinning, removal, and offline installation.

### Multi-Agent delegation

**User need:** some investigations and independent work packages benefit from
parallel or specialized execution.

**Invariant:** one user-facing Turn remains accountable for the result. Each
delegate needs explicit context, budgets, tool boundaries, cancellation, and
evidence; delegation must not create a hidden second permission or persistence
system.

**Open decisions:** scheduling, result aggregation, nested delegation, shared
workspace conflicts, per-Agent budgets, and how the foreground-operation model
should evolve.

### More model providers

**User need:** model choice should not be limited to the two initial adapters.

**Invariant:** a provider implements the shared message, streaming, usage,
reasoning, tool, error, and cancellation contracts. Provider-specific payloads
must not leak into Agent nodes, tools, storage, protocol, or the TUI.

**Open decisions:** the next concrete provider, model-catalog ownership,
capability negotiation, context limits, and credential validation.

### Search tools

**User need:** coding work often requires current web or documentation facts
that are not present in the Workspace.

**Invariant:** Web Search and Web Fetch would enter the same Registry, Policy,
Executor, result, event, timeout, and approval path as every other tool. Remote
content remains untrusted context.

**Open decisions:** provider choice, network allowlists, citation preservation,
privacy, caching, output limits, and uncertain-result handling.

## Later directions

### More memory providers

A second external memory adapter may justify a provider abstraction. Until then,
Awesome deliberately keeps local Memory and Mem0 Cloud explicit rather than
inventing a framework around one implementation. Any new provider must define
consent, identity, retention, deletion, failure isolation, and what data leaves
the machine.

### Scheduled tasks

Scheduled work should reuse Agent, tool, Skill, Memory, trust, budget, and
recovery contracts. A scheduler must not become a second execution engine.
Unattended approvals, credential availability, missed schedules, concurrent
Workspace changes, and result delivery require a separate threat and product
model before implementation.

### Gateway messaging

Messaging platforms could adapt typed Application intents and events so users
can submit work and receive progress outside the terminal. The adapter must not
duplicate Agent behavior, hold a hidden transcript, or weaken Workspace and
permission boundaries. Identity, tenancy, delivery ordering, and secret
handling remain open.

### Optional isolated tool backend

An optional Docker or equivalent backend could provide stronger process and
filesystem isolation for users who need it. It would sit below Tool Executor
policy; trust, approvals, command hard-deny, output bounds, and auditing remain
required above it.

The current local backend is not an operating-system sandbox. Adding an
isolated backend requires explicit platform support, mount and network policy,
image lifecycle, performance, and recovery semantics rather than a simple
configuration switch.

## How roadmap items become product contracts

A direction moves into current documentation only after:

1. the user need and non-goals are written down;
2. architecture ownership and dependency direction are decided;
3. public configuration, protocol, storage, and security changes are explicit;
4. failure, cancellation, recovery, and compatibility behavior is tested;
5. user guides, reference pages, architecture pages, and release notes agree;
6. packaging and cross-platform evidence is available for the supported scope.

This rule keeps aspirational design out of runtime documentation and prevents a
prototype from becoming an accidental public contract.
