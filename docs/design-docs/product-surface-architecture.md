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
| Shared client / surface layer | API clients, stream parsing, command metadata, command validation, error formatting, and client-side adapters. | Textual widgets, Web components, provider calls, graph state transitions, or tool execution. |
| API / service layer | Threads, messages, conversation turns, Run creation, model-backed turns, semantic inspection resources, structured errors, request ids, and stream contracts. | Textual behavior, Web layout, prompt widgets, or direct UI state. |
| Runtime / AgentLoop | Bounded model-to-tool execution, middleware, hooks, budget checks, capability checks, observability, and durable Run transitions. | UI layout, slash-command parsing, client autocomplete, or frontend-specific response shaping. |
| Provider layer | Provider-neutral model calls, streaming, usage, routing, fallback, and error classification. | UI state, tool grants, durable graph authority, or conversation storage. |
| Persistence | Durable thread, message, Run, tool, artifact, budget, trace, and extension state. | Per-widget scratch state or transient render buffers. |

## Conversation Flow

The normal chat flow is:

1. The TUI accepts user input in the active thread.
2. The TUI calls the shared conversation client.
3. The shared client sends a semantic request to the API.
4. The API service persists the user message and starts a conversation turn.
5. Backend service code invokes the configured provider or runtime path.
6. The API streams normalized events such as `message.delta`,
   `message.completed`, `usage.updated`, and `error`.
7. The shared client parses the stream and passes typed events to the TUI.
8. The TUI renders those events and keeps only presentation state locally.
9. The backend persists the final assistant message and usage evidence.

The TUI must be able to reconnect or resume from persisted thread messages
instead of relying on its local transcript cache.

## Coding Run Bridge Flow

A Coding Run is an explicit execution mode inside a thread, not the default
representation of every ordinary chat turn.

The Run bridge flow is:

1. A user command, model-mediated confirmation, or future Web action requests a
   Coding Run from the current thread context.
2. The API validates the thread context, repository/workspace, model profile,
   sandbox profile, and capability policy.
3. The API delegates Run creation to the existing Run intake/runtime services.
4. Runtime events remain the execution source of truth.
5. Conversation services project high-level Run lifecycle events into the
   thread transcript.
6. Artifacts remain run-scoped for audit but become discoverable from the
   containing thread.
7. Cancellation, approvals, retries, validation, and recovery stay API/runtime
   owned.

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
- Treat every chat message as a Coding Run.
- Create slash-command-named API routes.

## Task Dependency Order

The Product Surface Phase should proceed in this order:

1. Lock this architecture contract and structural guardrails.
2. Add durable conversation state and API endpoints.
3. Add model-backed conversation streaming.
4. Connect the full-screen TUI to real conversation streaming.
5. Promote command metadata into a shared registry and add autocomplete.
6. Bridge conversation threads to explicit Coding Runs.
7. Replace surface command stubs with real capability APIs.
8. Harden structured errors, cancellation, retry, diagnostics, and E2E.

