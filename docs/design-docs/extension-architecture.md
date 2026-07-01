# Extension Architecture

## Purpose

Extensions let `awesome_agent` use capabilities that are not compiled into the
core runtime, such as MCP tools, skill packages, and packaged community tools.
The extension architecture must preserve the runtime kernel's core properties:
durable execution, least-privilege tool access, auditability, token-only
budgets, and AgentLoop middleware observability.

An extension declares inventory. It does not grant authority. Authority remains
an effective capability decision derived from user policy, route policy, team
assignment state, skill requests, actor kind, and runtime execution state.

## Scope

The extension phase covers:

- versioned extension catalogs;
- skill package discovery and runtime views;
- MCP tool discovery and execution through the existing tool boundary;
- packaged community tools;
- extension diagnostics and hardening.

The extension phase does not introduce ACP agents. It also does not treat
Subagents as an independent extension subsystem. Subagents remain part of the
agent-team assignment model, and any future Subagent behavior change must
preserve the Leader, Teammate, Verifier, and assignment authority boundaries.

Models and sandboxes may eventually use similar configuration and health
patterns, but they are not model-visible tool extensions. Model providers stay
in the provider layer. Sandbox implementations stay execution backends used by
tool execution.

## Core Principles

- Discovery creates inventory, not authority.
- Running Runs pin one `extension_catalog_version`; hot reload affects new
  Runs by default.
- Root team Runs pin the catalog version for their child Runs.
- Extension tools are hidden unless explicit policy or assignment grants make
  them visible.
- Skills may request tools and capabilities, but skills never grant execution
  authority.
- MCP tools must execute through `ToolExecutor`; MCP clients must not be called
  directly from AgentLoop logic.
- `before_tool_exposure` is an independent hook and the only place that creates
  the model-visible tool set.
- `before_model_call` consumes a `ToolExposureSet`; it must not recompute tool
  authorization.
- `before_tool_call` validates one exact invocation; it must not allow a tool
  that was not exposed.
- Denied/error/state hooks are observational unless a graph action explicitly
  models a durable transition.
- Extension configuration uses declarative config plus allowlisted adapter
  factories. Arbitrary reflection loading is not part of the runtime contract.

## Architecture

```text
Extension Control Plane
  config
  -> allowlisted source adapters
  -> discovery
  -> health snapshots
  -> versioned ExtensionCatalog

Runtime Pinning
  Run / root team Run pins extension_catalog_version
  child Runs inherit the root catalog version

Resolution
  catalog snapshot
  + actor kind
  + route
  + assignment grants
  + allowed_skills
  + skill requested tools
  + allowed_tools / deferred_tools / promoted_tools
  -> before_tool_exposure
  -> CapabilityResolver
  -> ToolExposureSet

Execution
  before_model_call consumes ToolExposureSet
  model emits tool calls
  before_tool_call checks invocation state
  ToolExecutor executes built-in, MCP, or community handlers
  after_tool_call / on_tool_denied / on_tool_error records evidence
```

## Extension Control Plane

The control plane owns configuration loading, source adapter construction,
discovery, health monitoring, and catalog publication. It is not AgentLoop
middleware and it does not run once per AgentLoop operation.

Lifecycle hooks:

```text
before_extension_config_load
after_extension_config_load
before_extension_discovery
after_extension_discovery
on_extension_discovery_error
before_extension_catalog_publish
after_extension_catalog_publish
on_extension_health_changed
```

Discovery runs at startup, explicit refresh, config change, file watcher
change, and recovery refresh. It should not run at the start of every Run.

The catalog is immutable after publication. A new discovery result publishes a
new catalog version. Existing Runs continue to use their pinned version unless
a future explicit durable refresh action is introduced.

## AgentLoop Data Plane Hooks

AgentLoop hooks operate on a pinned catalog snapshot and current runtime
context. They do not discover extensions.

```text
before_agent_run
after_agent_run
on_agent_error

before_tool_exposure
after_tool_exposure

before_model_call
wrap_model_call
after_model_call

before_tool_call
wrap_tool_call
after_tool_call
```

`before_tool_exposure` is independent. It computes the model-visible tool set
from catalog inventory, skill requests, assignment grants, actor kind, route,
and capability policy. `before_model_call` consumes the resulting
`ToolExposureSet` when constructing the model request. `before_tool_call`
validates one invocation against the exposure result plus current execution
state, such as approval, budget, cancellation, health, temporary grants,
argument policy, and sandbox availability.

## Observation And Coordination Hooks

Observation hooks record evidence and diagnostics. They do not override the
authoritative decision points.

```text
on_tool_denied
on_tool_error
on_state_transition
on_handoff
on_child_run_created
```

`on_tool_denied` is fire-and-forget. It may create audit, metrics,
diagnostics, user notification, or recovery evidence. It cannot convert a deny
decision into execution.

## Extension Catalog

An `ExtensionCatalog` records:

- catalog version;
- source id, source type, trust level, and health snapshot;
- extension manifests;
- normalized tool inventory;
- skill manifests;
- compatibility metadata for routes and actor kinds;
- redacted diagnostics metadata.

Tool names from external sources must be namespaced. MCP tools use the form:

```text
mcp.<source_id>.<tool_name>
```

Community tools use the form:

```text
community.<package_id>.<tool_name>
```

Built-in tools keep their existing names.

## Skills

A skill package provides instructions, context references, examples, and
capability requests. It does not grant authority.

```text
SkillManifest:
  id
  version
  source_id
  instructions
  context_refs
  requested_tools
  required_capabilities
  compatible_actor_kinds
  compatible_routes
  risk_level
```

The runtime flow is:

```text
allowed_skills
-> load matching SkillManifest from pinned catalog
-> create SkillRuntimeView
-> collect requested_tools and required_capabilities
-> before_tool_exposure
-> CapabilityResolver
-> ToolExposureSet
```

If a skill requests a tool that the assignment or policy does not grant, the
skill instructions may still be injected when compatible, but the requested
tool remains hidden. Diagnostics must show the requested tool and the denial
reason.

## MCP

MCP servers are external tool sources. Discovery produces inventory only.
Discovered MCP tools are hidden by default.

The runtime flow is:

```text
MCP source config
-> MCP discovery
-> normalized namespaced ToolSpec inventory
-> ExtensionCatalog
-> explicit grant request
-> before_tool_exposure
-> CapabilityResolver
-> ToolExposureSet
-> before_tool_call
-> ToolExecutor
-> MCP adapter call
```

MCP execution must preserve approval, timeout, cancellation, observability,
durable tool invocation records, bounded result serialization, and artifact
offload. MCP errors are tool errors, not provider fallback events.

## Configuration

Extension configuration is declarative and uses allowlisted source types:

```yaml
extensions:
  sources:
    - id: local-skills
      type: skill_directory
      path: .agents/skills
      trust: project
    - id: playwright
      type: mcp_stdio
      command: npx
      args: ["@playwright/mcp"]
      trust: user
```

The runtime may reject a source when the type is unknown, the trust level is
insufficient, the path escapes allowed roots, the command is disallowed, or
the manifest cannot be normalized into the shared contracts.

## Durable Evidence

Runs and child Runs store or expose enough information to explain extension
behavior:

- pinned `extension_catalog_version`;
- source ids and versions used by visible tools;
- skill ids and versions used in model context;
- exposed and denied tool reasons;
- exact tool invocation records;
- MCP/community source health at invocation time;
- approval, timeout, cancellation, and budget outcomes;
- redacted diagnostics without raw secrets, prompts, or unbounded tool output.

## Extension Phase Tasks

The extension phase proceeds in this order:

1. Task 43: Extension catalog and lifecycle substrate.
2. Task 44: Independent tool exposure hook and extension-aware capability
   resolution.
3. Task 45: Skill manifest and skill runtime view.
4. Task 46: Skill context injection through AgentLoop hooks.
5. Task 47: MCP stdio discovery adapter.
6. Task 48: MCP stdio execution through `ToolExecutor`.
7. Task 49: MCP Streamable HTTP/SSE-compatible transport and auth expansion.
8. Task 50: Community tool packages.
9. Task 51: Extension operations hardening.

Each task must be independently testable and must not weaken existing graph,
AgentLoop, capability, observability, token-budget, approval, or team
assignment invariants.
