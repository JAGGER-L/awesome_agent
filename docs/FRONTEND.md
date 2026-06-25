# Frontend

## Current Demo

`demo/index.html` is a standalone, backend-free interface demonstration. It
exists to evaluate product information architecture and visual direction before
choosing the production frontend stack.

Run it locally:

```powershell
.\.venv\Scripts\python.exe -m http.server 4173 -d demo
```

The demo uses mock data and does not execute commands, persist changes, or call
the FastAPI service.

## Visual Direction

The interface is a restrained, warm, document-oriented workspace inspired by
Claude's product character:

- warm off-white surfaces with dark neutral text
- restrained terracotta accent
- serif headings paired with a compact system sans-serif UI
- thin borders, low shadows, and radius no greater than 9px
- dense operational layout without decorative gradients or atmospheric media

This is directional inspiration, not a copy of Claude branding or proprietary
interface assets.

## Workspace Layout

```text
run navigation
  -> run header and controls
  -> goal and runtime metrics
  -> Agent Team topology
  -> dynamic Todo tree and event trace
  -> selected Agent detail, conversation, tools, approval, artifacts
```

The desktop layout uses a run sidebar, central workspace, and Agent detail
panel. At tablet width the detail panel is removed. At mobile width the sidebar
becomes an explicit navigation drawer and the operational panels stack.

## Demonstrated Interactions

- select Leader, Teammates, Verifier, and Subagents
- inspect per-Agent model, context, workspace, and conversation
- filter Todos by active/completed state
- filter events by tool/message type
- open, approve, or deny a mock command request
- pause and resume the mock run
- switch mock run history
- open mobile navigation

## Production Requirements

The production frontend remains outside V1 scope. Runtime APIs and events must
support a future interface that displays:

- run timeline
- agent topology and lifecycle
- per-agent conversations
- team mailbox
- dynamic task tree
- tool execution and progress
- approval requests
- artifacts and diffs
- verification and rework
- memory operations

Frontend-facing schemas must be versioned and must not expose secrets.
