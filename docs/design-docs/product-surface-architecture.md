# Product Surface Architecture

This document defines how the local full-screen TUI, API Server, future Web
frontend, shared client layer, conversation services, and runtime kernel fit
together. It is a product-surface contract, not a Textual implementation note.

## Product Positioning

`awesome_agent` is becoming a local coding-agent product over a durable runtime
kernel. The runtime already owns auditable Runs, AgentLoop execution,
capability enforcement, sandbox execution, extension catalogs, and operational
evidence. The product surface phase adds the missing user-facing conversation
contract around that kernel.

The product must avoid separate chat implementations for the TUI, Web, and API
clients. A surface may have different presentation affordances, but all
conversation, Run, tool, memory, upload, artifact, MCP, sandbox, and
observability authority must come from shared backend contracts.

## Full-Screen TUI Decision

The local `awesome` command remains a full-screen TUI. This phase intentionally
does not introduce an inline terminal chat mode.

Ordinary input is the primary product entry. Users should not need to know
whether a response is a lightweight model turn, a tool-capable coding
execution, or a background runtime task. The system creates the appropriate
durable Run semantics behind the conversation turn.

The full-screen TUI may own:

- input widgets, focus, keyboard bindings, scrolling, and layout;
- slash-command suggestion rendering and shortcut presentation;
- presentation-only transcript cache while streaming;
- local first-run guidance when the backend is not yet configured.

The full-screen TUI must not own:

- model provider calls;
- AgentLoop construction or execution;
- tool authorization or tool execution;
- durable conversation history;
- Coding Run state transitions;
- MCP, skill, memory, upload, artifact, or sandbox policy.

## Boundary Table

| Layer | Owns | Must not own |
| --- | --- | --- |
| Full-screen TUI | Presentation, focus, keyboard interaction, scrollback, autocomplete UI, command display, and local render state. | Model calls, AgentLoop imports, provider routing, tool policy, durable state, or backend-only resource decisions. |
| Future Web frontend | Browser presentation, Web-specific navigation, and rendering of the same stream and command metadata. | A separate chat engine, Web-only AgentLoop path, or duplicated command semantics. |
| Shared client / surface layer | API clients, embedded local clients, stream parsing, command metadata, command validation, error formatting, and client-side adapters. | Textual widgets, Web components, provider calls, graph state transitions, or tool execution. |
| Embedded local runtime host | Local composition of services for the `awesome` TUI profile, including thread context, model/runtime service access, LocalSandbox defaults, and foreground execution ownership. | Textual widgets, Web components, direct UI rendering, hidden global daemons, or a separate chat engine. |
| API / service layer | Threads, messages, conversation turns, Run creation, model-backed turns, semantic inspection resources, structured errors, request ids, and stream contracts for HTTP/Web/remote clients. | Textual behavior, Web layout, prompt widgets, or direct UI state. |
| Runtime / AgentLoop | Bounded model-to-tool execution, middleware, hooks, budget checks, capability checks, observability, and durable Run transitions. | UI layout, slash-command parsing, client autocomplete, or frontend-specific response shaping. |
| Provider layer | Provider-neutral model calls, streaming, usage, routing, fallback, and error classification. | UI state, tool grants, durable graph authority, or conversation storage. |
| Persistence | Durable thread, message, Run, tool, artifact, budget, trace, and extension state. | Per-widget scratch state or transient render buffers. |

## Conversation Flow

The normal local TUI flow is:

1. The TUI accepts user input in the active thread.
2. The TUI calls the shared surface client.
3. The surface client is either embedded local mode or explicit HTTP mode.
4. The shared service layer records the user message and starts a turn.
5. A planner chooses the execution mode: lightweight model turn,
   tool-capable coding Run, background Run, or resume of an interrupted Run.
6. Runtime/provider services stream normalized events such as
   `run.started`, `reasoning.delta`, `message.delta`, `tool.completed`,
   `artifact.created`, `message.completed`, `usage.updated`, and `error`.
7. The surface client passes typed events to the TUI.
8. The TUI renders those events and keeps only presentation state locally.
9. Services persist final messages, usage, Run evidence, and artifact links.

The TUI must be able to reconnect or resume from persisted thread messages
instead of relying on its local transcript cache.

## Run Semantics

Ordinary input is the primary execution route. Every user turn has durable Run
semantics, but not every turn is a heavy coding execution. A simple question may
create a lightweight Run that only records model streaming, interruption, usage,
and completion evidence. A coding request may create a tool-capable Run that
uses AgentLoop, tools, sandbox execution, validation, approvals, artifacts, and
recovery. A long-running request may run in the background.

The Run flow is:

1. A user input, continuation request, explicit `/run`, or future Web action
   requests execution from the current thread context.
2. Services validate the thread context, repository/workspace, model profile,
   sandbox profile, and capability policy.
3. The planner selects lightweight chat, foreground coding, background coding,
   or resume.
4. Runtime events remain the execution source of truth.
5. Conversation services project user-meaningful Run lifecycle events into the
   thread transcript.
6. Artifacts remain run-scoped for audit but become discoverable from the
   containing thread.
7. Cancellation, pause, resume, approvals, retries, validation, and recovery
   stay service/runtime owned.

`/run` is an advanced/manual command for forcing or inspecting execution mode.
It is not the ordinary way to make the agent work.

## Slash-Command Metadata

Slash commands are surface interaction syntax. The API should expose semantic
resources such as threads, messages, Runs, models, tools, memory, MCP, and
artifacts. It should not expose routes named after slash commands.

Command metadata belongs in a shared surface/client layer so the TUI and future
Web can use the same definitions for:

- `/help`;
- aliases;
- argument hints;
- autocomplete candidates;
- availability checks;
- command validation;
- command execution dispatch.

UI layers may render suggestions differently, but they must not fork command
meaning.

## Non-Goals

- Add inline terminal chat.
- Let the TUI call model providers directly.
- Let the TUI import AgentLoop or graph modules.
- Add Web-specific backend forks.
- Require `/run` for normal coding-agent work.
- Treat every ordinary question as a heavy tool-capable coding execution.
- Create slash-command-named API routes.

## Task Dependency Order

The Product Surface Phase should proceed in this order:

1. Complete the embedded local runtime host and shared surface client boundary.
2. Make streaming, pause, cancel, and resume nonblocking and consistent.
3. Fix thread/session UX so ordinary input, `/new`, `/threads`, and `/resume`
   feel like conversation navigation.
4. Polish transcript rendering into a compact coding-agent chat surface.
5. Surface provider reasoning as bounded collapsible thought UI.
6. Make model routing explainable through structured metadata.
7. Align startup docs and CLI help around `awesome`, `make dev`, and Docker API
   profiles.
