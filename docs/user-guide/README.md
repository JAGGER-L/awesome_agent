# User Guide

This guide is for day-to-day use after the first successful session. It is
organized around decisions users make: how to state a task, when to grant
authority, how to inspect work, and how to recover when an operation stops.

## A Reliable Working Loop

```text
orient -> request -> observe -> approve when needed -> review -> verify -> continue
```

### 1. Orient

Start from the project root and inspect the current session:

```text
/workspace
/status
/permissions
/tools
```

Use `/resume` when the task depends on earlier conversation. Use `/new` when a
new problem would otherwise inherit irrelevant history.

### 2. Make the Request Testable

State the outcome, constraints, and evidence of completion. For example:

```text
Fix the duplicate retry in the payment worker. Keep the public API unchanged,
add a regression test for concurrent failures, and run only the affected tests.
```

Reference a narrow file with `@path` when it is the authoritative entry point.
Avoid prescribing every implementation step unless those steps are part of the
requirement; the agent needs room to inspect the current architecture.

### 3. Observe and Approve Deliberately

Tool activity shows what Awesome is reading, changing, or running. In Request
approval mode, compare the operation and target in each prompt with your stated
goal. “Allow all edits during this session” applies only to ordinary Workspace
writes; it does not include deletes, shell commands, MCP, or future sessions.

If you already chose an exact shell command, `! command` runs it directly
through Core without asking the model to plan it. That direct form is explicit
user authority: normal shell approval is skipped, while command hard denials,
timeouts, cleanup, redaction, and audit still apply.

### 4. Review the Artifact

After file changes:

```text
/diff
/status
```

Read the assistant's verification statement and distinguish tests that ran
from tests merely recommended. `/diff` covers recorded built-in file changes;
it cannot summarize arbitrary effects produced by a shell command or external
MCP service.

### 5. Continue or Recover

Ask a follow-up in the same Thread when it depends on the current context.
Start a new Thread for unrelated work. Use `/undo` only after reading the
ChangeSet and understanding its reversibility. When an external operation has
an uncertain outcome, inspect the target system before choosing Retry.

## Common Workflows

### Explore a repository

```text
Map the request path from the CLI to persistence. Cite the main modules and do
not modify files.
```

Built-in Workspace reads are automatically allowed in a trusted Workspace;
MCP and unknown extension capabilities still ask. `/context` shows the sources
used for the response.

### Implement a focused change

```text
Add a timeout to the import request. Preserve the existing error type, add a
failing regression test first, and run that test after the fix.
```

Use Accept edits if you want ordinary creates and modifications to proceed
without repeated prompts while keeping deletes and shell execution gated.

### Run a known command

```text
! git status --short
```

Use direct commands for commands you have already reviewed. Use natural
language when you want the model to decide which check is relevant.

### Compare or restore changes

```text
/diff
/undo
/redo
```

Conflict checks prevent an old ChangeSet from overwriting later edits. See
[Review, undo, and redo](changes.md).

## Choose the Right Page

- [Commands and interaction](commands.md): launch forms, slash commands,
  keyboard behavior, queueing, and direct shell.
- [Permissions and safety](permissions.md): trust, approval modes, hard
  denials, and the absence of an OS sandbox.
- [Tools and shell execution](tools-and-shell.md): how tools are selected,
  validated, executed, timed out, and presented.
- [Review, undo, and redo](changes.md): ChangeSets, conflicts, reversibility,
  and crash recovery.
- [Configuration and credentials](configuration.md): model setup, credential
  sources, user settings, and trusted Workspace restrictions.
- [Troubleshooting](troubleshooting.md): symptom-based diagnosis and safe
  recovery.

Extensions have separate guides: [Memory](../extensions/memory.md),
[Skills](../extensions/skills.md), and [MCP](../extensions/mcp.md). Exact syntax,
schemas, and limits belong to the [Reference](../reference/README.md).
