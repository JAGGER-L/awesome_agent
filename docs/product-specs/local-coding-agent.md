# Local Coding Agent

## User Outcome

A user can submit a coding task locally, observe how the Leader plans it, see
which Teammates and Subagents are created, inspect tool results and task
progress, approve dangerous commands, and receive only independently verified
team output.

## V1 Capabilities

- solo and team execution
- dynamic task tree
- Teammate mailbox
- bounded Subagent delegation
- mandatory team verification
- Docker command execution
- PostgreSQL resume
- traceable conversations, tools, artifacts, and approvals
- optional built-in and Mem0 memory
- Typer CLI and local FastAPI inspection API

## Non-Goals

- production deployment
- production frontend
- multi-user authentication
- multiple model providers
- recursive delegation
- LangSmith or LangGraph Agent Server

