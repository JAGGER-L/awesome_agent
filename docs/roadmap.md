# Roadmap

## V1.0.0 now

Awesome is one local `awesome` Ink interface backed by a private Python Core.
It supports trusted workspaces, DeepSeek/Kimi, the initial file/process tool
kernel, Change Journal, resumable local Threads, SQLite/LangGraph checkpoints,
Skills, MCP, and two independently optional memory layers.

V1.0.0 is a limited pilot on Apple Silicon macOS, Windows 11 x64, and WSL2
Ubuntu 24.04 x64. The immediate goal is to learn whether installation, first
trust, real coding Turns, resume, and recovery are understandable and reliable
for a few users.

## Next: pilot feedback and quality

- fix reproducible installation, startup, Provider, tool, and recovery defects;
- improve actionable diagnostics and bounded transcript/tool rendering;
- strengthen retained contract, install, and real-user-flow tests;
- refine context/compression quality using observed coding tasks;
- evaluate memory usefulness and privacy with opt-in pilot evidence.

## Later, only after demonstrated demand

- an optional Docker tool-execution backend for isolation;
- an API adapter reusing the Python Application facade;
- an IDE adapter reusing the same protocol and event contracts;
- additional Providers, tools, or memory services only when a concrete user
  need justifies their maintenance cost.

Awesome is not evolving by default into a general Agent Platform, hosted
runtime, distributed scheduler, or multi-user data service. Those are separate
products and require separate evidence and decisions.
