# Configuration

Awesome reads a small strict configuration surface. Unknown or duplicate YAML
keys are errors; configuration is loaded when Core starts, so restart Awesome
after editing files or environment variables. Thread choices made by commands
such as `/model`, `/thinking`, and `/skill` apply without a restart.

## Locations

| Source | Scope |
| --- | --- |
| built-in defaults | Safe product defaults. |
| `<AWESOME_HOME>/config.yaml` | User Provider, budget, memory, Skill, and MCP settings. |
| `<workspace>/.awesome/config.yaml` | Trusted workspace budget restrictions, disabled Skills, and MCP declarations. |
| `<AWESOME_HOME>/.env` | User-owned secret file. |
| process environment | Secret and approved startup overrides. |

`AWESOME_HOME` defaults to `%LOCALAPPDATA%\Awesome` on Windows and `~/.awesome`
on macOS/WSL2. `AWESOME_HOME` may override that location.

Workspace configuration is not read until the path is trusted. It cannot set
credentials, memory, or a Provider, and it can only reduce user budget values.

## User configuration

```yaml
version: 1
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
memory:
  local_file_memory: false
  mem0_cloud: false
skills:
  disabled: []
mcp_servers: []
```

Supported model IDs are `deepseek/deepseek-v4-flash`,
`deepseek/deepseek-v4-pro`, `kimi/kimi-k2.6`, and `kimi/kimi-k2.5`.

## Workspace configuration

```yaml
version: 1
budgets:
  model_calls: 24
skills:
  disabled: []
mcp_servers: []
```

For each budget, the effective value is the lower of the user value and an
optional workspace value.

## Defaults and hard limits

The default total context budget is 262,144 tokens (256K). Default per-turn
budgets are 32 model calls, 64 tool calls, 1,800 active seconds, 2 Provider
retries, and 2 compressions.

Configuration cannot exceed these hard limits:

- model calls: 256;
- tool calls: 512;
- active execution: 21,600 seconds (six hours);
- provider retries: 6;
- compressions: 10.

The model's actual context limit may reduce the effective context budget.

## Environment and precedence

Secrets are `DEEPSEEK_API_KEY`, `MOONSHOT_API_KEY`, and `MEM0_API_KEY`. A
process environment value wins over the same name in `<AWESOME_HOME>/.env`.
Secrets are never read from a workspace `.env` or printed by `/config`.

Approved startup overrides are:

- `AWESOME_MODEL`: one supported full model ID;
- `AWESOME_THINKING`: `on` or `off`;
- `AWESOME_SKILL`: `auto`, `off`, or a discovered Skill name.

For a new thread, approved startup overrides win over stored/user defaults.
Thereafter thread selections are durable. User configuration supplies product
defaults; trusted workspace configuration may only restrict budgets and
disable or add workspace extensions.
