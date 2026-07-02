# Runtime Roadmap

This roadmap is the durable product and architecture roadmap for the
`awesome_agent` runtime. It is not a session handoff and it is not a local
execution plan.

Local agent plans under `.codex/exec-plans/` may decompose work, record
evidence, and guide one implementation effort. They must not silently change
the durable roadmap, architecture direction, phase ordering, or exit
conditions in this file.

Historical task details that no longer guide current sequencing live in
[`runtime-roadmap-archive.md`](runtime-roadmap-archive.md).
Future roadmap updates must follow
[`roadmap-writing-rules.md`](roadmap-writing-rules.md).

## Project Positioning

`awesome_agent` is a local coding-agent runtime kernel. Its goal is not to
clone DeerFlow. DeerFlow remains a useful reference for role separation, team
orchestration, and workflow clarity, but this project optimizes for a different
center of gravity:

- durable local execution that survives worker crashes, process restarts, and
  resumptions;
- auditable side effects for repository mutation, shell execution, validation,
  approvals, cancellation, and recovery;
- least-privilege tool governance for solo agents, leaders, teammates,
  subagents, verifiers, and external extension points;
- bounded model-to-tool loops with explicit token, call-count, active-time,
  retry, and rework limits;
- a stable Graph, AgentLoop, middleware, hook, state, capability, and
  observability boundary that future provider, MCP, skills, and product
  surfaces must reuse.

The long-term product should feel like a reliable local agent runtime that can
scale from a solo coding run to a coordinated team run without losing audit
clarity or permission control.

## Long-Term Architecture Target

The architecture target is a small durable kernel surrounded by replaceable
policy and extension layers.

| Layer | Durable responsibility | Must not own |
| --- | --- | --- |
| API / CLI | Intake, inspection, operator commands, readiness, approval and cancellation surfaces. | Agent reasoning loops or route-specific policy. |
| Worker / Dispatch | Claiming Runs, leases, heartbeats, fencing, retry eligibility, and process-level execution ownership. | Tool permissions or model routing policy. |
| Graph | Checkpoint identity, durable state transitions, interrupts, resume, cancellation, child-run creation and waits, result persistence, patch aggregation, mailbox persistence, terminal projection. | Cross-cutting policy, prompt shaping, retry strategy, observability policy, or tool authorization logic. |
| AgentLoop | One bounded model-to-tool operation for one agent role. | Durable child-run orchestration or global roadmap policy. |
| Middleware / Hooks | Context assembly, observability, budget checks, permission checks, tool exposure, approval waits, validation policy, retry wrapping, error classification, artifact offload, and terminal cleanup. | Durable graph authority or hidden side effects outside audited boundaries. |
| Capability Resolver | Effective tool policy for exposure, execution, inspection, validation, and temporary grants. | Prompt-only permission conventions. |
| State / Repositories | Facts needed after crash, resume, audit, UI/API inspection, or idempotent recovery. | Per-turn scratch data, prompt scaffolding, transient retry state, or provider-local heuristics. |
| Provider Layer | Provider-neutral model calls, streaming, usage reporting, continuation, error classification, routing, and fallback. | Tool authority, graph state transitions, or commercial billing policy. |

Durable state is reserved for facts that must survive a crash or be inspected
later. Ephemeral state is preferred for prompt scaffolding, in-memory retries,
model-call shaping, and per-turn scratch policy unless a middleware crosses an
explicit durable boundary.

## Runtime Governance Invariants

These invariants override older task-level handoffs when they conflict:

- Graph modules own durable coordination only.
- AgentLoop owns model-to-tool iteration for one agent operation.
- Middleware and hooks own cross-cutting runtime policy.
- Leader authority is durable and explicit. The Leader assigns Teammates,
  grants capabilities, observes mailbox audit evidence, integrates results,
  decides bounded replanning, and creates the Verifier. The Leader does not
  directly create Subagents.
- Teammates own bounded execution and may create Subagents only when their
  durable assignment grants delegation.
- Subagents stay read-only, scoped, mailbox-less, non-delegating, and
  accountable to their owning Teammate.
- Tool access is an effective capability decision, not a prompt convention.
- Global tools, Leader-only tools, Teammate tools, Subagent tools, deferred
  tools, promoted tools, and temporary grants must converge through a shared
  resolver before they are exposed to a model or executed.
- Runtime budgets are token, reasoning-token, active-time, call-count, retry,
  and rework limits. Monetary amount limits are intentionally outside the
  runtime kernel.
- New extension surfaces must reuse the same capability, budget,
  observability, audit, and AgentLoop boundaries as built-in tools.

## Roadmap Principles

The roadmap is ordered by dependency risk:

1. Stabilize the kernel before expanding the ecosystem.
2. Put durable coordination in Graph and cross-cutting policy in middleware.
3. Make tool permission a shared resolver decision before adding more tools.
4. Make observability and typed context available before provider fallback.
5. Treat local `.codex` plans as execution details, not architecture decisions.
6. Convert directional future phases into numbered tasks only when the previous
   phase has executable evidence and a clear exit condition.

This ordering prevents later provider, MCP, skills, and product work from
amplifying weak observability, weak permissions, or metadata-heavy middleware
contracts.

## Product Surface Phase

Tasks 57-75 completed the startup, sandbox, first-run configuration,
conversation API, first real full-screen TUI chat, slash-command registry,
thread-scoped Run bridge, surface capability APIs, and product-hardening
groundwork. The active phase now corrects the remaining product gap: local
`awesome` must behave like a complete coding-agent product entry, not like a
thin shell that requires users to understand API servers, Workers, or `/run`
before ordinary work can begin.

This phase keeps `awesome` as a full-screen TUI. It intentionally does not add
an inline terminal chat mode. Ordinary text input is the primary execution
entry. Every user turn should have durable Run semantics, from lightweight
model chat to tool-capable coding execution. `/run` remains available only as
an advanced/manual execution control, not the required path for normal agent
work.

The architectural requirement is to keep the TUI thin while making the local
product complete: the TUI may own presentation, focus, shortcut handling, and
command suggestions, but it must not own model chat, AgentLoop, tool calling,
durable conversation history, or product policy. Local embedded TUI mode and
HTTP API mode must reuse the same service/runtime contracts; they differ by
transport and execution ownership, not by product semantics.

The profile and storage baseline remains specified in
[`runtime-profiles-and-startup.md`](../design-docs/runtime-profiles-and-startup.md):

- Docker API and local API development profiles default to AIO Docker.
- Local `awesome` TUI defaults to LocalSandbox for trusted local use.
- Model-visible generated files use `/mnt/user-data/workspace/`.
- Host thread workspaces persist under
  `~/.awesome-agent/threads/<thread_id>/workspace/`.
- Run audit artifacts persist under
  `~/.awesome-agent/runs/<run_id>/artifacts/`.
- Repository-root `output/` and `e2e-output/` are not formal runtime output
  locations.

Current product surface sequence:

| Task | Phase | Status | Purpose | Exit condition |
| --- | --- | --- | --- | --- |
| Task 76 | Embedded Local Runtime Host | Active | Make `awesome` default to an embedded local runtime host where ordinary input can create resumable durable Runs without a running HTTP API. | TUI talks through `SurfaceClient`; local mode uses `LocalRuntimeHost`; HTTP mode remains explicit via `--api-url`; ordinary input is the primary execution entry; `/run` is advanced/manual; no TUI path imports provider, graph, or AgentLoop internals. |
| Task 77 | Nonblocking Streaming And Resume | Planned | Make local and HTTP execution stream incrementally without freezing the TUI, and make interrupted ordinary turns resumable. | Message deltas repaint before completion, slow commands run outside Textual handlers, `Ctrl+C` pauses/cancels active execution with partial output preserved, and `continue` / `继续` / `/resume` resumes the last resumable Run when available. |
| Task 78 | Thread Session And Continuation UX | Planned | Make thread navigation feel like conversation management instead of backend state dumping. | First ordinary input auto-creates a thread from launch context; `/new`, `/threads`, and `/resume` switch/load conversations cleanly; duplicate titles are distinguishable; full UUIDs and container paths stay out of normal transcript output. |
| Task 79 | Transcript UI Redesign | Planned | Make the full-screen TUI read like a coding-agent chat product rather than a raw log or operator dashboard. | User input, assistant output, command results, system notices, errors, run/tool events, and artifacts have distinct compact rendering; welcome chrome stays minimal; `/help` and long output never remove the input box. |
| Task 80 | Reasoning Thought UI | Planned | Surface provider reasoning as a compact collapsible thought block. | Provider reasoning events normalize into stream events; TUI shows active thought, collapsed completion timing, and `Ctrl+O` expansion; reasoning remains bounded, optional, and separate from assistant answer text. |
| Task 81 | Model Routing Diagnostics | Planned | Make configured/requested/observed model routing explainable. | `/models` and turn completion metadata show configured model, provider, base URL, env key presence, requested model, response model, and response id where available; model self-description is not treated as authority; no price/cost fields appear. |
| Task 82 | Startup Contract Cleanup | Planned | Align docs, CLI help, quickstart, and roadmap with product startup semantics. | `awesome` is documented as the local full-screen TUI entry; `make dev` is local API development; `make docker-start` is Docker API deployment; `awesome-agent start` is fallback/debug only; `/run` is not documented as required for normal work. |

Completed productization groundwork:

| Task | Phase | Status | Purpose | Exit condition |
| --- | --- | --- | --- | --- |
| Task 57 | Productization | Done | Locked runtime profiles, startup UX, workspace storage, and sandbox defaults in durable design docs and roadmap. | Roadmap and design docs define Docker API, local API development, and local TUI profiles, including sandbox defaults, workspace/artifact storage, and non-goals. |
| Task 58 | Sandbox | Done | Refactored sandbox provider contracts around LocalSandbox and AIO Docker, deleting the old one-shot Docker backend instead of preserving compatibility. | Shell execution and validation select only `local` or `aio-docker`; tests prove API cannot silently use LocalSandbox and no `docker-run` backend remains. |
| Task 59 | Startup | Done | Added cross-platform Makefile-driven Docker and local API startup commands. | `make docker-init`, `make docker-start`, `make check`, `make install`, `make setup-sandbox`, and `make dev` exist and docs reference them as the primary API startup path. |
| Task 60 | CLI | Done | Defined durable Thread/Conversation entry concepts and scaffolded the `awesome` interactive entrypoint and semantic slash-command model. | `awesome` launches the local interactive surface, first-run configuration is reachable, and slash commands map to semantic operations. |
| Task 61 | TUI | Done | Replaced the operator-console layout with a chat-first local coding-agent TUI direction. | The TUI supports the first chat-oriented commands and keeps API operations semantic, while real model chat remains a Product Surface Phase dependency. |
| Task 62 | Sandbox | Done | Implemented the standalone AIO Docker sandbox image and HTTP service foundation. | The sandbox service builds, exposes `/health` and `/execute`, runs commands inside `/mnt/user-data/workspace`, bounds output, handles timeouts, and preserves thread-scoped workspace state in service-level tests. |
| Task 63 | Sandbox Integration | Done | Wired AIO Docker into runtime, Compose, Makefile, and health checks as the default API sandbox. | API/Worker profiles execute shell tools through the AIO HTTP client, Compose starts the sandbox service, and `make docker-init` / `make docker-start` prepare and run it. |
| Task 64 | Verification | Done | Added AIO sandbox E2E evidence, cleaned up old one-shot Docker assumptions, and recorded LocalSandbox security debt. | Local API, Docker API, and TUI flows can generate and preserve a simple HTML artifact; no one-shot Docker backend remains; LocalSandbox unrestricted execution is tracked as explicit technical debt. |
| Task 65 | CLI Context | Done | Made `awesome` usable from any user project directory by treating launch cwd as the default thread context. | The TUI starts with the launch repo/workspace context and ordinary input no longer blocks on manual repository selection. |
| Task 66 | TUI Commands | Done | Simplified the full-screen TUI toward a Claude/Codex-style command surface. | The TUI exposes the accepted slash-command set and keeps the surface chat-first rather than an operator dashboard. |
| Task 67 | First Run | Done | Added first-run model/config guidance for the local TUI. | `awesome init`, `/config`, `/models`, and welcome guidance explain resolved config paths and missing API-key env vars without writing secrets. |
| Task 68 | Product Surface Architecture | Done | Locked the CLI/TUI/API/Web product-surface boundary and retained full-screen TUI as the local client. | Design docs and roadmap define TUI as presentation, shared client as protocol adapter, API/services as conversation authority, and AgentLoop/runtime as execution authority; no UI path calls provider or AgentLoop internals directly. |
| Task 69 | Conversation State And API | Done | Added durable thread, message, and conversation-turn state that can serve TUI now and Web later. | API can list/create/get threads, persist launch repo/workspace/model/sandbox context, append/list thread messages, and resume by id or title through a repository-backed service rather than process-local state. |
| Task 70 | Conversation Streaming Service | Done | Added model-backed conversation turns with a stable client stream event contract. | A conversation turn persists the user message, invokes the configured model through backend service code, streams `message.delta` / completion / error events, records usage and trace ids, and persists the assistant response without involving TUI-specific logic. |
| Task 71 | Full-Screen TUI Real Chat | Done | Connected the full-screen TUI to the shared conversation client and removed fake local chat acknowledgements. | Ordinary TUI messages create backend conversation turns, render streamed model responses, keep the input visible and focused after long command output such as `/help`, and pass headless layout/focus tests. |
| Task 72 | Shared Slash Command Registry | Done | Promoted slash-command metadata into a reusable surface contract with autocomplete. | `/help`, parsing, validation, aliases, keyboard autocomplete, and command execution use one shared registry; typing `/` and filtered prefixes show selectable suggestions; future Web can consume the same command metadata. |
| Task 73 | Conversation-Scoped Coding Runs | Done | Made Coding Runs an explicit execution mode inside a conversation thread. | The TUI can start a Run from the current thread context, Run events project back into the thread transcript, artifacts remain run-scoped but thread-discoverable, and cancellation/status stay API-owned. |
| Task 74 | Surface Capability APIs | Done | Replaced TUI command stubs with real API-backed status for models, tools, skills, MCP, memory, uploads, artifacts, usage, and config. | Slash commands read shared client/API resources instead of hard-coded empty values, and API inspection surfaces expose enabled/disabled state, health, grants, and redacted configuration. |
| Task 75 | Product Hardening And E2E | Done | Added the product reliability layer around the full-screen TUI and conversation APIs. | Structured errors, request/trace ids, cancel/retry/interrupt, `awesome doctor`, contract tests, TUI focus/layout tests, and local/Docker E2E prove the TUI/API path can create a simple HTML artifact through the real product path. |

Completed post-kernel setup:

| Task | Phase | Status | Purpose | Exit condition |
| --- | --- | --- | --- | --- |
| Task 40 | Operations | Done | Added a redacted runtime diagnostics surface over existing durable evidence. | `GET /runs/{run_id}/diagnostics` and `awesome-agent diagnostics <run-id>` summarize run status, dispatch, events, agents, token ledgers, model calls, tool invocations, validation reports, team child evidence, and observability evidence without creating a parallel state machine or exposing raw prompts, secrets, full tool output, or monetary fields. |
| Task 41 | Provider | Done | Wired provider routing into production Worker runtime graph construction. | Solo read-only, solo modifying, scoped team, distributed Leader, team-role, and team-verifier production paths receive route-aware provider resolvers while graph modules keep the provider-resolver injection boundary and tests can still inject direct fake providers. |
| Task 42 | Team Intelligence | Done | Added recovery metrics and team tuning evidence as a read-only operational projection. | `GET /runs/{run_id}/recovery-metrics` and `awesome-agent recovery-metrics <run-id>` summarize recovery actions, failure kinds, team roles, Verifier rework, provider/model outcomes, and token budget pressure without automatic recovery-policy mutation, provider ranking, or monetary fields. |

Extension sequence:

| Task | Phase | Status | Purpose | Exit condition |
| --- | --- | --- | --- | --- |
| Task 43 | Extension | Done | Added the extension catalog and lifecycle substrate. | Fake/local extension sources can publish versioned catalog inventory, Runs pin an `extension_catalog_version`, catalog inspection works, and refreshed catalogs affect new Runs without changing running Runs. |
| Task 44 | Extension | Done | Added an independent tool exposure hook and extension-aware capability resolution. | `before_tool_exposure` produces a `ToolExposureSet`; `before_model_call` consumes it without recomputing authorization; `before_tool_call` cannot execute tools outside the exposure set; denied exposure reasons are inspectable. |
| Task 45 | Skills | Done | Turned `allowed_skills` into parsed skill manifests and runtime views. | Skill packages can declare instructions, context refs, requested tools, required capabilities, actor/route compatibility, and risk; skills request capabilities but never grant execution authority. |
| Task 46 | Skills | Done | Injected skill context through AgentLoop hooks with budget and observability controls. | Compatible skill instructions/context enter model requests through `before_model_call`, large context is bounded or offloaded, skill ids/versions are observable, and requested tools still require explicit grants. |
| Task 47 | MCP | Done | Added MCP stdio discovery as an extension source. | Configured stdio MCP servers can be discovered into namespaced catalog tool inventory, default visibility is denied, source health is inspectable, and no MCP tool executes yet. |
| Task 48 | MCP | Done | Executed granted MCP stdio tools through `ToolExecutor`. | Granted MCP tools execute only after exposure and invocation checks, with approval, timeout, cancellation, durable invocation records, bounded results, and observability. |
| Task 49 | MCP | Done | Expanded MCP transports and authentication. | Stdio and Streamable HTTP/SSE-compatible MCP sources share the same catalog/exposure/executor model, secrets are redacted, source health uses background checks, and reconnect/backoff behavior is bounded. |
| Task 50 | Community Tools | Done | Added packaged community tool sources. | Allowlisted local tool packages can declare tools that normalize into catalog inventory and execute through the same exposure, capability, approval, and `ToolExecutor` path. |
| Task 51 | Operations | Done | Hardened extension operations and diagnostics. | Operators can inspect catalog diffs, source health history, extension denial/error metrics, stale catalog warnings, and structural tests prove extensions cannot bypass resolver/executor boundaries. |
| Task 52 | Extension Config | Done | Added project-level extension configuration and the default project skill root. | `awesome-agent.yaml` can declare skill roots and MCP sources, repository-root `skills/` auto-discovers project skills, stdio MCP env pass-through stores env names only, and discovered extensions still do not grant tool authority. |
| Task 53 | Documentation | Done | Reset documentation governance and public README entry points. | Docs have a reader-oriented map, documentation governance rules, rewritten bilingual README entry points, and a manual quickstart path without changing runtime behavior. |
| Task 54 | Quick Start | Done | Added a README-ready local Quick Start path. | Quick Start now explains prerequisites, `.env`, `awesome-agent.yaml`, `skills/`, local PostgreSQL, migrations, API/Worker startup, readiness, probe verification, diagnostics, and first read-only run. `scripts/quickstart.ps1` automates the local Windows path without requiring a model key for the required success check. |
| Task 55 | Quickstart | Done | Added Docker/API/Web quickstart matrix and documented current local/Docker inspection lanes. | README, quickstart, operations docs, Docker Compose API/Worker services, and structural tests cover the supported lanes. |
| Task 56 | TUI | Done | Added the first API-backed local TUI operator console. | `awesome-agent tui` can inspect Runs through API endpoints without direct database writes. |

## Architecture Debt Carried Forward

| Item | Current disposition | Forward action |
| --- | --- | --- |
| P2: typed middleware contract | Complete for the kernel phase. `MiddlewareContext` exposes focused typed envelopes for trace, capability subject, assignment, token budget, handoff, and error classification; metadata remains only for annotations and compatibility. | Keep new cross-cutting behavior on typed envelopes. Task 38 may enrich the capability envelope with the shared effective-policy decision, but must not reintroduce route-local permission metadata as the authority. |
| P3: migrate team routes | Complete for forward distributed routes. `team-coding`, `team-role`, and `team-verifier` enter `TeamAgentLoop` and shared middleware; graphs retain durable coordination. | Keep slimming graph-owned policy opportunistically, but do not reopen P3 as a broad migration task. |
| P4: unified tool permission | Complete for the kernel phase. Task 31 added the resolver foundation; Task 38 made effective policy visible through API inspection/team contexts and enforceable at the shared executor boundary. | Future tools, MCP, skills, provider-side tools, and temporary grants must enter through resolver inputs and pass `EffectiveToolPolicy` to exposure and execution helpers. |
| P5: team hardening | Complete for local validation, same-child validation rework, patch conflict recovery, stress coverage, mailbox collaboration, bounded Leader plan repair, policy-backed recovery budgets, and read-only recovery metrics. | Continue with production evidence collection and explicit calibration changes only after recovery metrics show stable provider/model and team-role patterns. |

## Post-Product-Surface Long-Term Plan

The following phases are directional. They are not committed task numbers until
this roadmap is updated through change control.

| Phase | Direction | Entry criteria | Exit shape |
| --- | --- | --- | --- |
| Provider Ecosystem Phase | Expand provider routing, fallback, model profiles, and provider-quality feedback. | Task 41 and Task 42 complete; production runtime graph construction uses route-aware provider resolvers and recovery outcomes are measurable. | Provider decisions are reliable, explainable, retry-safe, and tuned by measured runtime outcomes rather than hard-coded optimism. |
| Operations Phase | Improve dashboards, alerts, trace exploration, recovery metrics, and readiness diagnostics. | Task 40 and Task 42 complete; durable evidence can be inspected through redacted diagnostics and recovery-metrics projections. | Operators can diagnose latency, failure class, budget pressure, provider quality, recovery behavior, and worker health without reading raw logs. |
| Web And Multi-Surface Phase | Add a Web frontend and richer multi-client workflows over the same conversation, Run, command, tool, artifact, and memory contracts used by the full-screen TUI. | Product Surface Phase exits with stable Conversation API, shared client stream events, command metadata, and TUI evidence. | Web, TUI, and API clients share backend contracts without duplicating AgentLoop, model chat, tool, or policy logic. |
| Team Intelligence Phase | Improve Leader planning, assignment quality, Verifier calibration, and recovery tuning. | P5 evidence exists across real team runs and provider metrics. | Team behavior improves through bounded, observable policy changes rather than hidden prompt growth. |

## Completed Milestones Summary

Detailed historical task notes are archived in
[`runtime-roadmap-archive.md`](runtime-roadmap-archive.md).

| Range | Milestone | Summary |
| --- | --- | --- |
| Tasks 01-06 | Durable solo foundation | Runtime contracts, repository intake, PostgreSQL queueing, Worker execution, provider-neutral model protocol, and checkpointed solo read-only loops. |
| Tasks 07-12 | Safe solo modification | Central tool execution, Docker shell, durable side-effect records, approvals, cancellation, deterministic validation/rework, lifecycle projections, and solo observability. |
| Tasks 13-18 | Team and operations foundation | Real team E2E, managed workspace cleanup, readiness checks, context compaction, distributed child-run skeleton, root-aware team budgets, and payload compaction. |
| Tasks 19-24 | AgentLoop architecture migration | Graph-version removal, ThinGraph/AgentLoop contracts, solo and team AgentLoop middleware migration, OTel span instrumentation, and durable graph versus policy boundary cleanup. |
| Tasks 25-35 | Distributed team hardening and governance | Teammate-local validation, same-child rework, patch conflict recovery, multi-worker stress, mailbox collaboration, roadmap lock, capability resolver foundation, bounded Leader replanning, policy-backed recovery budgets, provider-aware token accounting, and token-only budget governance. |
| Tasks 36-42 | Kernel completion and operational evidence | AgentLoop observability, typed middleware context, capability-policy convergence, provider routing/fallback, runtime diagnostics, production provider routing integration, and recovery metrics. |
| Tasks 43-52 | Extension architecture | Versioned extension catalogs, independent tool exposure hooks, skill manifests/context injection, MCP discovery/execution/transports, community tools, diagnostics, project extension config, and repository-root `skills/` discovery. |
| Tasks 53-56 | Documentation, quickstart, and first TUI | Documentation governance, bilingual README entry points, local Quick Start, Docker/API/Web quickstart matrix, and the first API-backed local TUI operator console. |
| Tasks 57-67 | Productization groundwork | Runtime profiles, sandbox defaults, Makefile startup, AIO Docker sandbox service/integration, local TUI entrypoint, cwd repo/workspace context, simplified full-screen slash-command surface, and first-run config guidance. |
| Tasks 68-75 | API-backed product surface | Product-surface architecture, durable conversation state, model-backed conversation streaming, real full-screen TUI chat, shared slash-command registry, thread-scoped Run bridge, surface capability APIs, structured errors, request ids, cancel/retry, diagnostics, and product E2E. |

## Change Control

- Roadmap edits must follow
  [`roadmap-writing-rules.md`](roadmap-writing-rules.md).
- Every new numbered task must map to one row in this roadmap, one open
  technical debt item, or a documented production incident.
- If a future task needs to reorder the roadmap, first update this document in
  a standalone governance change that states the reason, dependency impact, and
  displaced work.
- Local plans under `.codex/exec-plans/active/` may decompose work but must not
  silently change architecture direction, phase ordering, or exit conditions.
- A task may close or narrow debt only when executable evidence exists in tests,
  health checks, traces, durable query APIs, or documented operational checks.
- Do not start broad provider, MCP, skill, UI, or DeerFlow-style expansion while
  a kernel-phase task remains open unless that work is the explicit purpose of
  the active kernel task.
- Do not raise quality scores unless executable evidence exists in tests,
  health checks, traces, durable query APIs, or documented operational checks.
