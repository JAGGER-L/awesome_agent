# ADR 0006: Python Core and Ink stdio boundary

- Status: Accepted and implemented
- Date: 2026-07-10

## Decision

Python owns Application and Agent Core. TypeScript Ink + React owns input,
rendering, keyboard behavior, and presentation state only. A versioned
JSON-RPC/NDJSON stdio protocol carries intents, lifecycle calls, interactions,
transcript reads, cancellation, and typed events.

Ink never calls models, LangGraph, tools, storage, memory, Skills, or MCP
directly. There is no HTTP server in the local product. Future API or IDE
surfaces must adapt the same Application facade and event contracts.

## Consequences

Core rules are tested without Ink; TUI crashes cannot redefine durable state;
protocol compatibility is explicit. Packaging privately installs both Python
and Node runtimes behind the single `awesome` launcher.

## Rejected

An all-TypeScript rewrite violates the Python requirement. Duplicating Core in
Ink creates two authorities. HTTP adds ports, lifecycle, and authentication
without helping the local first-run path.
