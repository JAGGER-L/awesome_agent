# Slash command regression matrix

The runtime command authority is `CommandName` and `COMMAND_OWNERS` in
`src/awesome_agent/application/commands.py`. Protocol fixtures, the generated
Ink catalog, completion, Help, presenters, and the tests below must match that
authority exactly. This table documents verification; it is not another
runtime registry.

| Command | Owner | Input and result | Interaction and empty/error behavior | Focused coverage |
| --- | --- | --- | --- | --- |
| `/new` | Application | Bare command; typed Thread result | Replaces the transcript atomically; errors remain visible | `conversation_commands`, `controller` |
| `/resume` | Application | Bare picker or explicit Thread ID | Disabled/empty choices explain why; selected Thread replaces history | `conversation_commands`, `controller` |
| `/context` | Application | Typed Context rows | Empty categories still show budget; failures are errors | `context-usage-presenters` |
| `/compact` | Application | In-place progress then typed Compact result | No-op and failure replace the progress row | `change-presenters` |
| `/model` | Application | Provider/model picker | Missing credential enters Auth; unavailable source is explicit | `model-flow` |
| `/auth` | Application | Provider then source picker | Masked secret input; replace/delete; no silent source fallback | `auth-flow` |
| `/thinking` | Application | On/Off picker | Current selection is marked; failure is visible | `controller`, `catalog-presenters` |
| `/workspace` | Application | Typed workspace path | Shows only the active path | `catalog-presenters` |
| `/diff` | Application | Typed bounded Diff | Explicit no-changes state; invalid ChangeSet is an error | `diff-presenter` |
| `/undo` | Application | Typed Change result | Folded paths; no-op/conflict/error are distinct | `change-presenters` |
| `/redo` | Application | Typed Change result | Folded paths; no-op/conflict/error are distinct | `change-presenters` |
| `/tools` | Application | One row per effective Tool | Empty inventory is explicit; policy facts are typed | `catalog-presenters` |
| `/skills` | Application | List or mode picker | Empty/diagnostic states are visible | `catalog-presenters`, `controller` |
| `/mcp` | Application | Typed server rows | No configured servers is an explicit empty state | `catalog-presenters` |
| `/memory` | Application | Local/Cloud picker, then On/Off | Values are independent; unavailable Cloud reports its reason | `memory-flow` |
| `/status` | Application | Aligned product-status panel | Invalid snapshots fail the protocol contract | `status-doctor-presenters` |
| `/usage` | Application | Aligned usage rows | Zero usage remains visible | `context-usage-presenters` |
| `/doctor` | Application | Aligned diagnostic rows | Each check has a typed status | `status-doctor-presenters` |
| `/config` | Application | Effective sources and credential presence | Never renders secret values | `catalog-presenters` |
| `/permissions` | Application | Permission picker/result | Full access uses warning confirmation | `controller`, interaction tests |
| `/help` | Ink | Bare catalog or focused command | Ordinary transcript result; unknown name explains failure | `help` |
| `/theme` | Ink | Theme picker or explicit value | Escape returns to Composer | `local` |
| `/copy` | Ink | Copies the latest assistant response | Empty transcript and clipboard errors are visible | `local` |
| `/quit` | Ink | Graceful shutdown | Active work follows lifecycle confirmation rules | lifecycle tests |

Tab completion inserts only the executable bare command. Argument placeholders
appear only in Help usage. Unknown and removed commands produce an explicit
error and never silently no-op. Slash commands never use a hidden prompt to
submit an Agent Turn.

During a foreground Operation, every command in this table enters the same
session-only queue as natural input and `! shell`. Promotion remains FIFO;
empty-Composer recall remains LIFO. Commands that open a Picker or Approval
pause promotion, Thread transitions bind the following item to the new Thread,
and `/quit` is an ordered terminal barrier.
