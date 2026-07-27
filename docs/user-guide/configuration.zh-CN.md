# 配置与凭据

本页面向需要选择模型、管理凭据或更改小范围受支持配置的用户，重点说明安全工作流和优先级。完整 schema、默认值和字段约束见[配置参考](../reference/configuration.zh-CN.md)。

## 交互式选择应优先使用命令

正常使用时，通过 TUI 配置模型和凭据：

```text
/model
/auth
/config
/doctor
```

`/model` 为当前 Thread 选择 Provider 和模型，并更新未来 Thread 的用户默认值。`/auth` 通过遮罩输入添加、替换、删除或选择凭据来源。`/config` 报告来源和凭据存在性诊断，但不打印 secret。`/doctor` 按需执行 Provider 验证。

稳定的默认值、预算、禁用 Skills 和 MCP 声明应使用 YAML；不要用它代替 secret 输入。

## 配置来源

Awesome 读取以下来源：

| 来源 | 位置或形式 | 权限 |
| --- | --- | --- |
| 产品默认值 | 内置于 Core | 安全基础值 |
| 用户配置 | `<AWESOME_HOME>/config.yaml` | 用户默认值与扩展 |
| Workspace 配置 | `<workspace>/.awesome/config.yaml` | 受信项目限制与扩展 |
| Thread 状态 | 嵌入式应用数据库 | 持久模型、Thinking 和 Skill 选项 |
| 进程环境 | 经批准的变量名 | 凭据和启动覆盖项 |
| Awesome 管理的 secret | `<AWESOME_HOME>/.env` | 仅包含所选凭据值 |

`AWESOME_HOME` 在 Windows 上默认为 `%LOCALAPPDATA%\Awesome`，在 macOS/WSL2 上默认为 `~/.awesome`。只有确实希望使用独立的用户状态根时，才在启动前设置 `AWESOME_HOME`。

接受信任前不会读取 Workspace 配置。它不能选择 Provider、定义凭据或启用 Memory。其预算值只能收紧用户值；有效值取两者中的较小者。

建立信任后，`.awesome/config.yaml` 会通过与其它 Workspace 控制输入相同的有界 no-follow
边界读取。Reader 只接受一个不超过 1 MiB 的普通 UTF-8 文件，拒绝 NUL、link/reparse
point、hard link 和非普通节点，并固定、重新检查目录与文件身份。不安全、被替换、超限或
无效的 YAML 会使配置激活失败，而不会被跟随、截断或部分接受。参见
[配置参考](../reference/configuration.zh-CN.md#workspace-配置)。

## 如何解析一个 Turn

应用设置先加载，随后每个 Turn 冻结自己的有效事实：

```text
用户配置 + 受信 Workspace 限制
                     |
                     v
            应用默认值与限制
                     |
     +---------------+----------------+
     | 新 Thread 初始模型              |
     | 持久 Thread 选项                |
     | 用户默认值                      |
     +---------------+----------------+
                     |
                     v
       冻结的模型/thinking/skill/预算
```

对于新建 Thread，生产启动器会优先使用存在的 `AWESOME_MODEL`，然后使用用户默认值。如果两者都未设置，且恰好只有一个模型 Provider 拥有可用凭据，Awesome 会选择该 Provider 的 curated default。此后，Thread 的持久模型具有权威性，直到 `/model` 将其更改。Turn 执行到一半时不会更改其冻结的模型或预算。

Thinking 和 Skill 是通过 `/thinking` 与 `/skills` 更改的持久 Thread 选项；新 Thread 默认启用 Thinking，Skill 模式为 `auto`。

## 用户配置

最小用户文件可以设置模型和预算：

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

curated model ID 包括：

- `deepseek/deepseek-v4-flash`；
- `deepseek/deepseek-v4-pro`；
- `kimi/kimi-k2.6`；
- `kimi/kimi-k2.5`。

Kimi region 为 `cn` 或 `global`。在 [Kimi 中国区控制台](https://platform.kimi.com/console/api-keys)创建的密钥使用 `cn`；在 [Kimi 全球区控制台](https://platform.kimi.ai/console/api-keys)创建的密钥使用 `global`。DeepSeek 密钥来自 [DeepSeek API Key 页面](https://platform.deepseek.com/api_keys)。账户、密钥可用性、计费和网络访问仍是 Provider 侧前提。请求会把组装后的模型上下文发送给所选 Provider，因此请查看其当前条款、隐私政策和组织数据控制。Provider adapter、凭据变量和完整文档示例维护在[配置参考](../reference/configuration.zh-CN.md)中。

Web 能力与 model Provider 相互独立。它默认关闭，Tavily credential 始终来自
`TAVILY_API_KEY`；async HTTP client 使用 `trust_env=False`，所以会忽略环境 proxy 变量。
只有需要显式代理时才设置 `AWESOME_WEB_PROXY_URL`（或其已选择的 Awesome secret），并使用
`/web on|off|status|revoke` 管理；不要尝试通过 Workspace config 启用。Search query 与请求的
Fetch URL 会依据 Tavily 的[隐私政策](https://www.tavily.com/privacy)与
[平台条款](https://www.tavily.com/terms)发送给 Tavily。Fetch extraction 由 Tavily 云服务执行；
Awesome Core 不连接请求的目标。

Memory、Skill 与 MCP 示例分别见聚焦指南：[Memory](../extensions/memory.zh-CN.md)、[Skills](../extensions/skills.zh-CN.md) 和 [MCP](../extensions/mcp.zh-CN.md)。

## Workspace 配置

项目可以在信任后降低预算并声明项目扩展：

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

如果用户允许 32 次模型调用，而 Workspace 指定 24，则有效值为 24。如果 Workspace 指定更高值，也不会提高用户上限。这种非对称 merge 使仓库能够保护共享资源，又不能自行授予更多权限。

不要把 secret 或用户身份放入 `.awesome/config.yaml`；它是项目内容，可能被提交。

## 默认值与硬性护栏

概括来说，默认总上下文预算为 262,144 Tokens，每个 Turn 包含 32 次模型调用、64 次工具调用、2 次 Provider 重试、2 次压缩、8 次 Web 请求和 1,800 秒活动执行时间。配置仍受以下上限约束：

- 模型调用：256；
- 工具调用：512；
- 活动执行：21,600 秒；
- Provider 重试：6；
- 压缩：10。
- Web 请求：8。

所选模型的真实上下文窗口可能会降低配置的上下文总量。Core 还会先预留输出容量和安全余量，再推导有效输入预算。请用 `/context` 和 `/usage` 检查结果，不要假定 YAML 数字全部可用于输入。

## 凭据是来源，不是 fallback chain

Awesome 识别：

| 服务 | 环境变量 |
| --- | --- |
| DeepSeek | `DEEPSEEK_API_KEY` |
| Kimi | `MOONSHOT_API_KEY` |
| Mem0 Cloud | `MEM0_API_KEY` |

进程环境值与 Awesome 管理的值是两个独立来源。在做出任何显式选择前，如果 Environment 存在，Awesome 会选择它，否则选择可用的 Awesome-managed 值。`/auth` 记录选择后，该选择便具有权威性。如果它之后消失，Provider 会显示为 Unavailable；Awesome 不会静默回退到另一个来源。

这样可以检查凭据来源，也能防止陈旧的 shell 变量意外替换用户在 TUI 中选择的密钥。

### Environment 来源

在启动 shell 或其批准的 secret manager 中设置变量，再从该进程启动 Awesome。TUI 将 Environment 视为只读。避免使用会把密钥持久化到 shell 历史的命令。

### Awesome 管理的来源

在 `/auth` 中选择服务，选择 **Awesome API key**，然后使用遮罩输入。secret store 会在 `AWESOME_HOME` 下原子替换相应值。删除它会删除本地值，但不会吊销 Provider 侧密钥。

`.env` 中的值与 `config.yaml` 中选中的 source 会作为一项可从崩溃恢复的产品操作提交。
Awesome 会在 `AWESOME_HOME` 根目录临时保存一份不含 secret 的 journal，以及仅 owner 可访问的
完整 `.env` backup；验证两个文件后才删除恢复证据。重启时，系统会在加载 credential、检查
state 或询问 Workspace trust 之前解决中断操作。不要编辑或删除
`.provider-credential-transaction.json` 与 `.provider-credential-transaction.env`；若证据
不一致，启动会报告 `recovery_required`，而不会使用只更新了一半的 key。

保存 DeepSeek 或 Kimi 密钥时会执行简短验证。无效密钥不会保存。网络失败会提供显式选项，将其保存为 unverified。Mem0 可用性在扩展启用或使用时检查。

绝不要把密钥放在 slash-command 参数、chat request、Workspace 配置、`AGENTS.md`、Skill 或项目 `.env` 文件中。

## 模型环境覆盖项

`AWESOME_MODEL` 接受一个 curated 的完整 Provider/model ID，在新 Thread 的持久模型存储前完成初始化。无效或空值会使模型解析失败，而不是被忽略。

公开启动器不提供等价的 Thinking 或 Skill 环境覆盖项。请使用 `/thinking` 和 `/skills`；这些选项会持久化到 Thread，并应用于未来 Turn。

## 验证与重新加载行为

User YAML 必须是带 `version: 2` 的 mapping；Workspace YAML 仍是 `version: 1`。User version 1 会在内存中兼容读取，第一次受支持的写操作会将其原子升级。未知 key、重复 key、格式错误的 YAML、无效名称、不受支持的模型和超范围预算都会产生错误。Core 不会推断重命名字段。

手动编辑文件或进程环境后，会在下一次 Core 启动时加载。`/model`、`/auth`、`/memory`、`/thinking` 和 `/skills` 等 TUI 流程会持久化其支持的变更，并刷新相关实时状态，无需手动编辑文件。当前正在执行的 Turn 保留其冻结配置。

手动更改后：

退出 Awesome，编辑目标文件，并从系统终端重新启动：

```console
awesome --continue
```

然后在 Awesome TUI 中验证：

```text
/config
/doctor
```

如果验证失败，请修复指出的用户或 Workspace 文件；不要添加未知 key 并期待未来版本理解它们。

## 常见问题

- **未配置模型：**运行 `/model`，选择拥有可用凭据的 Provider。
- **所选来源不可用：**运行 `/auth`，恢复该精确来源，或显式选择另一个来源。
- **Workspace 配置被忽略：**确认信任以及精确的 `<workspace>/.awesome/config.yaml` 路径。
- **预算低于用户文件：**检查受信 Workspace 限制和模型上下文上限。
- **手动编辑没有效果：**重启 Core；Thread 级命令选项仍可能有更高优先级。

按错误恢复见[故障排查](troubleshooting.zh-CN.md)，全部字段与约束见[配置参考](../reference/configuration.zh-CN.md)。
