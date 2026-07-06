# Local Coding Agent

## Product Promise

`awesome` is a local terminal-first coding assistant for trusted project work.
It should understand the current project, make scoped edits, run checks, ask
before risky side effects, and leave reviewable evidence.

## Primary User

A developer working inside one local repository who wants an agent to help
inspect, edit, test, and explain code without losing control of the workspace.

## Core Workflow

1. Start `awesome` from the project root.
2. Send a plain user message.
3. Watch model reasoning, tool timeline, approvals, and result messages.
4. Approve or deny risky actions from the fixed approval control.
5. Review changed files and validation evidence.

## Product Contracts

- User messages create conversation turns; users do not select graph nodes.
- Runtime owns model calls, tools, approvals, cancellation, retry, and recovery.
- Tool side effects are visible through timeline and changed-file summaries.
- Approval resume is exact-bound; repeated matching actions may reuse only
  bounded command/path grants.
- `continue` resumes interrupted work and is not sent as a model message.

## Non-Goals

- Hosted multi-user service.
- Production authorization model.
- Web frontend before local CLI and API contracts stabilize.
- Prompt-only permission enforcement.

## Success Evidence

- Quickstart commands work on supported platforms.
- TUI product flows have unit or e2e coverage.
- Runtime approval, retry, cancellation, and recovery behavior has contract
  tests.
- Documentation links point to canonical user, operations, API, and
  architecture docs.
