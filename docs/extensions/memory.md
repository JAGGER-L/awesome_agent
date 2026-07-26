# Memory

Memory is optional long-term reference context. It is useful for stable user
preferences and durable workspace conventions. It is not conversation history,
an instruction channel, a secret store, or a replacement for checked-in
project documentation.

Prerequisites are a trusted workspace and, for Mem0 Cloud, the optional Memory
dependency plus a selected Mem0 credential. Both Memory backends are off by
default and can be enabled independently.

## Local and cloud Memory

| Property | Local file Memory | Mem0 Cloud |
| --- | --- | --- |
| Default | Off | Off |
| Scope | `user` and `workspace` | `user` and `workspace` |
| Storage | Markdown below `<AWESOME_HOME>` | Mem0, keyed by opaque Awesome identities |
| Recall | Every valid local document is considered | Semantic search using the current user input |
| Write path | Explicit command or Memory tool | Post-answer distillation; explicit search/remove commands |
| Entry limit | 2,000 characters; document limit 1,000,000 bytes | 500 characters per candidate; at most five candidates per distillation |
| Failure effect | Scope reports an error; file is not silently rewritten | Turn continues with a diagnostic |

Choose local Memory when the facts should remain inspectable and machine-local.
Choose Mem0 when semantic recall across many facts justifies sending bounded,
filtered data to an external service.

## Enable and inspect Memory

The interactive flow is the safest entry point:

```text
/memory
  -> Local memory
     -> On

/memory
  -> Cloud memory · Mem0
     -> On
```

Equivalent explicit commands are:

```text
/memory local on
/memory local off
/memory mem0 on
/memory mem0 off
```

Enabling or disabling either backend is a state mutation and is rejected while
a foreground operation is active. The selection is written to user
configuration and takes effect in the current session.

The local switch treats the configuration value, the in-memory service flag,
and all four Memory tool registrations as one state transition. Before writing
configuration, Core validates the complete candidate tool set against the
aggregate 128-tool and 1 MiB catalog bounds. If it cannot fit, the command
returns `tool_registry_limit` and leaves configuration, service state, and the
existing Registry unchanged; it never exposes a partial Memory tool set.

Inspect and mutate local entries with:

```text
/memory list user
/memory list workspace
/memory add user Prefer concise explanations with runnable examples.
/memory replace workspace memory_<32-hex-digits> Run unit tests before packaging.
/memory remove user memory_<32-hex-digits>
```

Search and remove cloud entries with:

```text
/memory mem0 search preferred test command
/memory mem0 remove <mem0-memory-id>
```

See the exact grammar in the [command reference](../reference/commands.md).

## Configuration

User configuration controls both backends:

```yaml
version: 1
memory:
  local_file_memory: true
  mem0_cloud: false
```

`mem0_user_id` also exists in the user schema, but Awesome creates and manages
that opaque `user_<32-hex-digits>` value when Mem0 is first enabled. Do not copy
another user's identifier into the file.

Mem0 requires a credential selected through `/auth mem0`. An environment value
and an Awesome-managed value are separate sources; changing or deleting the
selected source never silently falls back to the other one. Secret locations
and the complete selection rules are in the
[configuration reference](../reference/configuration.md).

Unlike DeepSeek and Kimi, `/auth mem0` does not make a remote validation request.
It validates local input/storage shape and saves the value; authentication
failure appears only when Mem0 initialization, recall, removal, or distillation
contacts the service. A saved Mem0 key is therefore not proof that the
credential is valid.

## Local file format

Local Memory uses two files:

```text
<AWESOME_HOME>/memory/USER.md
<AWESOME_HOME>/workspaces/<workspace_key>/MEMORY.md
```

Awesome owns only the marked section and preserves Markdown before and after
it. A generated section has this shape:

```markdown
# Notes I maintain myself

<!-- awesome-agent:managed-memory:start -->
<!-- memory:id=memory_0123456789abcdef0123456789abcdef -->
- Prefer focused diffs and explain any unverified risk.
<!-- awesome-agent:managed-memory:end -->
```

The file must be UTF-8, at most 1,000,000 bytes, contain at most one correctly
ordered marker pair, and contain unique generated entry IDs. Invalid managed
syntax makes the scope unavailable; Awesome does not guess how to repair it.

Every mutation is compare-and-swap:

```text
read exact bytes -> SHA-256 content_hash -> validate proposed entry
                 -> re-read hash -> atomic sibling replace
```

The hash prevents a command or Agent tool from silently overwriting a concurrent
manual edit. A mismatch returns `memory_conflict`; list the document again and
repeat the intended mutation against the new state.

Local Memory tools appear only while local Memory is enabled:
`memory_list`, `memory_add`, `memory_replace`, and `memory_remove`. Agent-driven
writes are described to the model as valid only for an explicit current-user
request. That is a model-facing instruction, not a runtime semantic classifier.
At runtime, mutations require the matching trusted workspace, Agent origin, and
an active Turn, then pass content policy and compare-and-swap checks. Their
`memory.read` and `memory.write` capabilities do not prompt through the three
workspace permission modes.

## What reaches the model

At Turn preparation, Awesome snapshots both local documents. Local entries are
deduplicated in `user` then `workspace` order. If Mem0 is enabled, the current
natural-language input becomes the search query; cloud results duplicating
managed local entries are removed. Handwritten Markdown outside the managed
marker region still reaches local context but is not part of the Mem0
deduplication input, so equivalent handwritten and cloud text can both appear.

Local and cloud failures deliberately differ. When local Memory is enabled, an
invalid or unreadable `USER.md` or `MEMORY.md` propagates from snapshot capture
and fails Turn preparation. Awesome does not silently continue with only one
local scope because that would hide which durable facts were used. The
coordinator terminalizes the already-created Turn as failed, emits one failed
Operation outcome, and attempts to remove its checkpoint. A cleanup failure is
logged without replacing the primary error; startup reconciliation retries
removal of any checkpoint left for the terminal Turn. A later Turn can start
normally after the document is repaired or local Memory is disabled. Mem0 search or
initialization failures instead become bounded diagnostics and the Turn
continues without the cloud source.

```text
USER.md -----\
               -> normalize + deduplicate -> untrusted context sources
MEMORY.md ----/                                |
                                                +-> Context Builder -> model
current input -> Mem0 search -> scoped results |
```

All Memory is labeled **untrusted reference context** and rendered as user-role
context, not mandatory system policy. Across all long-term Memory, the Context
Builder uses at most the smaller of 16,384 tokens and 10% of the effective input
limit. Within that pool the nominal shares and hard caps are:

| Source | Share | Hard cap |
| --- | ---: | ---: |
| User local Memory | 25% | 4,096 tokens |
| Workspace local Memory | 50% | 8,192 tokens |
| Mem0 recall | 25% | 4,096 tokens |

These are maxima, not reservations. Empty or duplicate sources consume no
context. Inspect the realized manifest with `/context`.

## Mem0 write and privacy pipeline

After a successful final answer, and only when Turn budget remains, Awesome may
use at most one additional model call to extract up to five stable facts. The
distiller receives redacted slices of the current user text (up to 4,000
characters) and final answer (up to 8,000 characters), not the entire
conversation or raw tool transcript.

Candidates then pass policy before upload. The policy rejects secret-like data,
credential and private absolute paths, raw code/diffs/tool output, executable
instructions, repository identifiers, and transient task status. Accepted
candidates are normalized and deduplicated by a scope-aware SHA-256 fact hash.
Mem0 is called with inference disabled.

Cloud records contain:

- an opaque Awesome user ID;
- `app_id: awesome-agent`;
- `scope: user|workspace`;
- a fact hash;
- an opaque workspace key only for workspace-scoped facts.

Recall returns at most eight records. Each Mem0 operation has a three-second
deadline. Authentication, rate-limit, timeout, malformed-response, and service
errors become bounded diagnostic codes; they do not turn cloud content into
local Memory or retry a write invisibly.

## Trust and tradeoffs

- Local files are readable user data. Protect `<AWESOME_HOME>` with normal OS
  account permissions and do not place secrets in Memory.
- Mem0 sends filtered facts and search queries to a third party. Review its
  data policy before enabling it.
- Memory content can still be wrong or adversarial. Labeling and role placement
  reduce authority; they do not make content true.
- Checked-in `AGENTS.md` or project documentation is better for mandatory team
  rules. A [Skill](skills.md) is better for a reusable procedure.

If Memory affects an answer unexpectedly, run `/memory list user`, `/memory
list workspace`, `/memory mem0 search <query>`, and `/context` in that order.
Disable the suspect backend before editing or removing entries.
