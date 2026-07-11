# stdio protocol and Ink

`tui/` is the only product surface. It uses Ink + React for terminal input,
layout, transcript state, pickers, theme, clipboard, and lifecycle UX. It does
not import or implement model, graph, tool, storage, memory, Skill, or MCP
behavior.

The TUI starts private `awesome-core` and exchanges JSON-RPC 2.0 requests plus
typed event notifications as newline-delimited JSON over stdin/stdout. The
protocol is versioned and bounded; malformed or oversized lines receive
protocol errors. Core logs use stderr so they cannot corrupt the event stream.

Intent flows from Ink to the Python `ApplicationFacade`. Events flow from
Application to Ink. Request IDs, operation IDs, Thread/Turn IDs, event
sequences, and typed interaction responses let Ink reconcile live output with
durable transcript reads after reconnect or resume.

Presentation state such as scroll position, theme, composer history, expanded
reasoning, and selection remains in the TUI. A future surface must adapt the
same facade/event contracts rather than becoming another execution authority.
