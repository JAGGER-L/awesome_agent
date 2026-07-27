# Configuration and Credentials

This page is for selecting a model, managing credentials, and changing the
small supported configuration surface. It emphasizes safe workflows and
precedence; the complete schema, defaults, and field constraints live in the
[configuration reference](../reference/configuration.md).

Web access is independent from the model Provider. It defaults off, always
reads the Tavily credential from `TAVILY_API_KEY`, and ignores ambient proxy
variables because its async HTTP client uses `trust_env=False`. Set an explicit
proxy only with `AWESOME_WEB_PROXY_URL` (or its selected Awesome secret), then
use `/web on|off|status|revoke`; do not hand-edit Workspace config to enable
it. Search queries and requested Fetch URLs are sent to Tavily under its
[Privacy Policy](https://www.tavily.com/privacy) and
[Platform Terms](https://www.tavily.com/terms). Tavily's cloud service performs
Fetch extraction; Awesome Core does not connect to the requested target.

## Prefer Commands for Interactive Choices

For normal use, configure models and credentials from the TUI:

```text
/model
/auth
/config
/doctor
```

`/model` selects a Provider and model for the current Thread and updates the
user default for future Threads. `/auth` adds, replaces, deletes, or selects a
credential source through masked input. `/config` reports source and
credential-presence diagnostics without printing secrets. `/doctor` performs
on-demand Provider validation.

Use YAML for stable defaults, budgets, disabled Skills, and MCP declarations;
do not use it as a substitute for secret input.

## Configuration Sources

Awesome reads these sources:

| Source | Location or form | Authority |
| --- | --- | --- |
| Product defaults | built into Core | Safe base values |
| User configuration | `<AWESOME_HOME>/config.yaml` | User defaults and extensions |
| Workspace configuration | `<workspace>/.awesome/config.yaml` | Trusted project restrictions and extensions |
| Thread state | embedded application database | Durable model, Thinking, and Skill choices |
| Process environment | approved variable names | Credentials and startup overrides |
| Awesome-managed secrets | `<AWESOME_HOME>/.env` | Selected credential values only |

`AWESOME_HOME` defaults to `%LOCALAPPDATA%\Awesome` on Windows and
`~/.awesome` on macOS/WSL2. Set `AWESOME_HOME` before launch only when you
intentionally want a separate user-state root.

Workspace configuration is not read until trust is accepted. It cannot select
a Provider, define credentials, or enable Memory. Its budget values can only
restrict the user's values; the effective value is the lower of the two.

After trust, `.awesome/config.yaml` is read through the same bounded no-follow
boundary used for other Workspace-controlled inputs. The reader accepts one
plain UTF-8 file up to 1 MiB, rejects NUL, links/reparse points, hard links, and
non-regular nodes, and pins and rechecks directory and file identities. Unsafe,
replaced, oversized, or invalid YAML fails configuration activation instead of
being followed, truncated, or partially accepted. See the
[configuration reference](../reference/configuration.md#workspace-configuration).

## How a Turn Is Resolved

Application settings are loaded first, then each Turn freezes its own effective
facts:

```text
user config + trusted Workspace restrictions
                     |
                     v
       application defaults and limits
                     |
     +---------------+----------------+
     | new-Thread initial model        |
     | durable Thread choices          |
     | user defaults                   |
     +---------------+----------------+
                     |
                     v
         frozen model/thinking/skill/budgets
```

For a newly created Thread, the production launcher uses `AWESOME_MODEL` when
present, then the user default. If neither is set and exactly one model Provider
has a usable credential, Awesome chooses that Provider's curated default.
Thereafter the Thread's durable model is authoritative until `/model` changes
it. A Turn does not change its frozen model or budget halfway through
execution.

Thinking and Skill are durable Thread choices changed through `/thinking` and
`/skills`; new Threads start with Thinking on and Skill mode `auto`.

## User Configuration

A minimal user file can set a model and budgets:

```yaml
version: 2
providers:
  default_model: deepseek/deepseek-v4-flash
  kimi_region: cn
budgets:
  model_calls: 32
  tool_calls: 64
  provider_retries: 2
  compressions: 2
  active_execution_seconds: 1800
  total_context_tokens: 262144
  web_requests: 8
web:
  enabled: false
  provider: tavily
  blocked_domains: []
```

The curated model IDs are:

- `deepseek/deepseek-v4-flash`;
- `deepseek/deepseek-v4-pro`;
- `kimi/kimi-k2.6`;
- `kimi/kimi-k2.5`.

Kimi region is `cn` or `global`. Use `cn` for a key created in the
[Kimi China console](https://platform.kimi.com/console/api-keys) and `global`
for a key created in the
[Kimi global console](https://platform.kimi.ai/console/api-keys). DeepSeek keys
come from the [DeepSeek API key page](https://platform.deepseek.com/api_keys).
Accounts, key availability, billing, and network access remain Provider-side
prerequisites. Requests send the assembled model context to the selected
Provider, so review its current terms, privacy policy, and organizational data
controls. The Provider adapters, credential variables, and complete document
example are maintained in the
[configuration reference](../reference/configuration.md).

Memory, Skill, and MCP examples belong to their focused guides:
[Memory](../extensions/memory.md), [Skills](../extensions/skills.md), and
[MCP](../extensions/mcp.md).

## Workspace Configuration

A project can lower a budget and declare project extensions after trust:

```yaml
version: 1
budgets:
  model_calls: 24
  active_execution_seconds: 900
  web_requests: 4
skills:
  disabled: []
mcp_servers: []
```

If the user allows 32 model calls and the Workspace specifies 24, the effective
value is 24. If the Workspace specifies a higher value, it does not raise the
user limit. This asymmetric merge lets a repository protect shared resources
without granting itself more authority.

Do not put secrets or user identity into `.awesome/config.yaml`; it is project
content and may be committed.

## Defaults and Hard Guardrails

At a glance, the default total context budget is 262,144 Tokens, with 32 model
calls, 64 tool calls, 2 Provider retries, 2 compressions, 8 Web requests, and 1,800 seconds of
active execution per Turn. Configuration remains bounded by:

- model calls: 256;
- tool calls: 512;
- active execution: 21,600 seconds;
- provider retries: 6;
- compressions: 10.
- Web requests: 8.

The selected model's real context window may lower the configured context
total. Core also reserves output capacity and a safety margin before deriving
the effective input budget. Use `/context` and `/usage` to inspect the outcome
rather than assuming the YAML number is fully available for input.

## Credentials Are Sources, Not Fallback Chains

Awesome recognizes:

| Service | Environment variable |
| --- | --- |
| DeepSeek | `DEEPSEEK_API_KEY` |
| Kimi | `MOONSHOT_API_KEY` |
| Mem0 Cloud | `MEM0_API_KEY` |

A process-environment value and an Awesome-managed value are two independent
sources. Before any explicit choice, Awesome selects Environment when present,
otherwise an available Awesome-managed value. After `/auth` records a choice,
that choice is authoritative. If it later disappears, the Provider is shown as
Unavailable; Awesome does not silently fall back to the other source.

This makes credential provenance inspectable. It also prevents a stale shell
variable from unexpectedly replacing a key the user selected in the TUI.

### Environment source

Set the variable in the launching shell or its approved secret manager, then
start Awesome from that process. The TUI treats Environment as read-only. Avoid
commands that persist the key in shell history.

### Awesome-managed source

Choose the service under `/auth`, select **Awesome API key**, and use the masked
input. The secret store writes the corresponding value under `AWESOME_HOME`
with an atomic replacement. Removing it deletes the local value but does not
revoke the Provider-side key.

The value in `.env` and the selected source in `config.yaml` are committed as
one crash-recoverable product operation. Awesome keeps a temporary non-secret
journal plus an owner-only full `.env` backup directly under `AWESOME_HOME`,
verifies both files, then removes the recovery evidence. On restart, it resolves
an interrupted operation before loading credentials, checking state, or asking
for workspace trust. Do not edit or delete
`.provider-credential-transaction.json` or
`.provider-credential-transaction.env`; if their evidence is inconsistent,
startup reports `recovery_required` instead of using a half-updated key.

Saving a DeepSeek or Kimi key performs a short validation. Invalid keys are not
saved. A network failure produces an explicit choice to save it unverified.
Mem0 availability is checked when the extension is enabled or used.

Never place a key in a slash-command argument, chat request, Workspace config,
`AGENTS.md`, Skill, or project `.env` file.

## Model Environment Override

`AWESOME_MODEL` accepts one curated full Provider/model ID and initializes a
new Thread before its durable model is stored. An invalid or empty value fails
model resolution rather than being ignored.

The public launcher does not expose equivalent Thinking or Skill environment
overrides. Use `/thinking` and `/skills`; those choices are persisted on the
Thread and applied to future Turns.

## Validation and Reload Behavior

User YAML must be a mapping with `version: 2`; Workspace YAML remains
`version: 1`. User version 1 is read compatibly in memory and the first
supported write upgrades it atomically. Unknown keys,
duplicate keys, malformed YAML, invalid names, unsupported models, and
out-of-range budgets are errors. Core does not infer renamed fields.

Manual file or process-environment edits are loaded on the next Core start.
TUI flows such as `/model`, `/auth`, `/memory`, `/thinking`, and `/skills`
persist their supported changes and refresh the relevant live state without a
manual file edit. A currently executing Turn retains its frozen configuration.

After a manual change:

Exit Awesome, edit the intended file, and restart from the system terminal:

```console
awesome --continue
```

Then validate from the Awesome TUI:

```text
/config
/doctor
```

If validation fails, fix the named user or Workspace file; do not add unknown
keys in the hope that a future version will understand them.

## Common Problems

- **Model not configured:** run `/model` and select a Provider with a usable
  credential.
- **Selected source unavailable:** run `/auth`, restore that exact source or
  explicitly choose the other one.
- **Workspace configuration ignored:** confirm trust and the exact
  `<workspace>/.awesome/config.yaml` path.
- **Budget is lower than the user file:** inspect the trusted Workspace
  restriction and model context limit.
- **Manual edit has no effect:** restart Core; Thread-level command choices may
  still have higher precedence.

Continue with [Troubleshooting](troubleshooting.md) for error-oriented recovery
or the [configuration reference](../reference/configuration.md) for every field
and constraint.
