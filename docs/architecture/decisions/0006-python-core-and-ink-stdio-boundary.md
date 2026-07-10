# ADR 0006: Python Core and Ink stdio Boundary

- Status: Accepted
- Date: 2026-07-10
- Scope: CLI, TUI, and future surfaces

## Context

The product needs a high-quality Ink + React TUI without moving agent behavior
into TypeScript. Future API and IDE surfaces should reuse the same core, but an
HTTP service is unnecessary for the local first-run path.

## Decision

The application host and Agent Core remain Python. The TUI is TypeScript using
Ink + React and owns only input, rendering, keyboard behavior, and presentation
state.

The first cross-process surface protocol is versioned JSON-RPC over stdio.
Conversation turns, application commands, cancellation, interaction responses,
and typed event notifications cross this boundary. The TUI never calls models,
LangGraph, storage, Mem0, MCP, or tools directly.

HTTP API and IDE integration are deferred until the application and event
contracts are stable. They will adapt to the same Python host rather than
becoming new runtime authorities.

## Consequences

- Business rules are tested in Python independently of Ink.
- Protocol compatibility is explicit and surface-neutral.
- TUI crashes do not redefine core state ownership.
- The project accepts a small Python/Node packaging boundary only at the
  product surface.

## Rejected Alternatives

- All-TypeScript rewrite: violates the required Python core.
- TUI imports or duplicates core behavior: creates two product authorities.
- Require HTTP for local TUI communication: adds service lifecycle, ports, and
  authentication concerns without product value.
