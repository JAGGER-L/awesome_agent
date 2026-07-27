# 安全与依赖

Awesome 是面向受信工作区的本地 coding agent。它有意在 host 上运行工具，以参与普通
开发者工作流。它不是安全沙箱、容器、虚拟机，也不是抵御恶意同权限进程的边界。

任何安全保证都必须先承认这一边界。工作区信任、权限提示、路径校验、进程清理和命令
circuit breaker 会降低特定风险；它们都不会把 host 执行变成隔离执行。

## 威胁模型

Awesome 的设计目标是抵御以下情况，或在其中安全失败：

- 在信任建立前意外激活项目控制的指令；
- 针对固定身份的工作区根、文件系统工具、`.awesome/config.yaml`、`AGENTS.md` 和
  Workspace Skills 发起替换、alias、链接与 reparse 攻击；
- 常见的结构化文件系统路径逃逸和敏感文件请求；
- 已知的灾难性 shell 命令及 wrapper；
- 过期审批跨越 Thread、Turn、Operation 或 permission mode；
- 畸形 provider、MCP、Skill、protocol 或持久化输入；
- 无界工具/模型输出、MCP schema/catalog、上下文、进程清理或请求并发；
- Turn 和受管文件 mutation 提交期间的普通进程崩溃；
- 支持的事件和工具输出中意外渲染 secret。

当前设计不声称可以抵御：

- 使用同一 OS 账户的恶意用户或进程替换本地状态；
- 任意恶意 shell 混淆；
- 命令有意逃出 POSIX process group；
- 需要 kernel compare-and-swap 或 mount boundary 的文件系统竞态；
- 已成功获批的 host 进程进行数据外传；
- 操作系统、终端、提供商、MCP server 或依赖分发渠道被攻破；
- 回滚每一个外部 shell、MCP、网络或服务副作用；
- SQLite、blob 目录与工作区文件之间的断电原子性。

如果所需威胁模型包含不受信代码执行，请使用外部 OS/container sandbox，不要把 Awesome
的 Full access mode 当作等价方案。

安全输入包括工作区身份/内容、工具参数、shell 文本、审批决策、扩展 catalog/result、
provider 输出、本地状态和 protocol frame。安全输出包括 allow/ask/deny 决策、有界事实
与诊断、受管进程清理以及保守恢复证据。没有任何控制会声称获准的 host 代码已被隔离。

## 分层决策模型

系统按顺序评估四个不同的问题：

```text
1. Workspace trust: may project-controlled content influence this session?
2. Hard safety: is this known operation forbidden even with user approval?
3. Permission: may this capability run now, or must the user decide?
4. Execution isolation: what can an allowed process access at the OS layer?
```

Awesome 实现前三层，目前不提供第四层。把这些层区分开，可以避免误导用户认为点击审批
就建立了沙箱。

这一分层遵循 [OpenAI](https://learn.chatgpt.com/docs/sandboxing#how-permissions-work)、
[Claude Code](https://code.claude.com/docs/en/permissions) 和
[Hermes](https://hermes-agent.nousresearch.com/docs/user-guide/security/) 文档中的相同通用
思路；Awesome 的确切行为只由本仓库定义。

## 项目影响必须晚于信任

启动时会规范化请求工作区并识别其身份。接受信任之前，Core 不会读取工作区配置、根目录
`AGENTS.md`、Workspace Skills、MCP 声明，也不会运行工具。只有接受会持久化；拒绝会
直接退出，而不会持久化一条负向 policy。

激活过程同时取得 path-key 与 physical-entity lease，然后重新校验已打开根目录身份。
路径名替换或其他 alias 无法创建第二个活动会话，并把第一个会话的 Turn 当成崩溃状态。

持久信任以规范工作区路径为 key。Physical identity 与 path/entity lease 是活动会话
guard，不属于持久化 trust record。两种绑定都不会把权限授予之后通过链接到达的任意内容。

建立信任后，`.awesome/config.yaml` 使用与 Workspace instructions 和 Skills 相同的 Core
no-follow reader 边界。它只接受一个不超过 1 MiB 的普通 UTF-8 文件，拒绝 NUL、link/
reparse point、hard link 和非普通节点，并固定、重新检查已打开目录与文件的身份。替换或
超限文档会使配置激活失败，而不是重定向或截断由项目控制的输入。

## 工作区指令

根目录 `AGENTS.md` 只在建立信任后加载，并且每个会话只加载一次。读取使用 `lstat`、
有界 open 和 open 后身份校验。它会拒绝 link/reparse point、读取期间替换、路径逃逸、
NUL、非 UTF-8、超过 32 KiB，或超过 8,192 token 与生效输入容量 10% 二者较小值的内容。

超限内容会被整份忽略，而不是截断成可能具有不同含义的规则。结构化诊断会持续显示在
Welcome、status line 和 `/doctor` 中，但不会让其他原本有效的配置变为无效。

## 授权与过期决策防护

三种 permission mode 按[工具与变更](tools-and-changes.zh-CN.md)中的规则管理已知
built-in capability。即使在 Full access 中，MCP 和未知 capability 也始终逐次询问。

`network.read` 同样会在每种 mode 下首次使用时 ASK。其审批只适用于一次或当前 Thread；
选择其他 Thread、重建 runtime、更改 permission mode、运行 `/web revoke` 或 `/web off`，
以及 shutdown 都会清除 Thread grant。Headless `--allow-network` 只能把当前活动 Turn 的
精确 prompt 解析为 allow-once，不能绕过硬拒绝。

审批会绑定到发起请求的 authority：

- Tool approval：Thread + Turn + Operation + interaction generation；
- Full access confirmation：选中的 Thread + permission generation；
- recovery decision：Thread + Turn；
- startup trust/reset：bootstrap state 与 interaction identity。

切换 mode 或 Thread 会使过期 Full access confirmation 失效并清除临时 grant。Full
access confirmation 默认选中安全的“保持当前模式”。Tool approval 是所属 Operation
的 continuation；其他 resolution 会先取得 foreground resolving lease，再重新校验并
改变状态。

TUI 展示 Core 提供的 operation 与 target。它不能通过修改显示文本制造 grant，也绝不
自行执行已批准调用。

## 文件系统边界

结构化文件系统工具 policy 会拒绝绝对和逃逸路径、link/reparse traversal、敏感凭据/
密钥名称和模糊 Windows 语法。有界 reader 会在读取后校验已打开对象的 identity、type、
size、link count 和 modification time。Mutation 使用固定 directory chain，并在可用处
使用 no-follow primitive。不跟随目标而删除最终 link 节点与遍历它不同；受测试覆盖的
安全平台路径支持前者。

递归 delete 会在删除任何内容前 inventory 完整目录树。嵌套 symlink、junction、reparse
directory、hard-linked file、容量越界或 identity mismatch 都会中止 inventory，使
预期工作区和外部目标保持不变。

这些机制能防止文件系统工具沿项目控制的链接到达工作区外。它们无法阻止最后一次身份
检查后的所有同权限竞态。在 POSIX 上，另一个进程还可以移动已打开的 parent directory；
descriptor-relative 操作仍指向该对象。更强保证需要 kernel 支持的 exclusive writer、
mount namespace 或 sandbox。

## Shell 边界

纯命令 policy 接收命令、显式 CMD/POSIX/PowerShell dialect、working-directory 候选以及
workspace。Direct 和 Agent 路径都会在 executor 准入时先评估请求词法目录；对于 Agent
调用，该步骤发生在任何审批之前，而 Direct 输入本身已是用户显式授权。随后 handler
解析并校验该目录身份，在 spawn 前立即用已校验的 resolved path 再次调用同一 policy。
第一阶段限制可以提出的内容；第二阶段凭真实路径证据阻止不安全执行。

系统会在严格深度/节点限制内检查已知 wrapper、复合命令、pipeline、换行、executable
alias、PowerShell encoded command 和部分字面量 Python 调用。所有模式都会拒绝已知的
灾难性删除、shutdown、elevation、formatting、device overwrite 和 fork-bomb pattern。
无法安全解析时也会拒绝。

这是不可禁用的防误操作 circuit breaker，不是完整 shell parser 或恶意代码分类器。
一旦允许，命令会以经过净化的 host environment 和 host account 权限执行。名称以常见
API-key/token/secret/password 后缀结尾的变量会从 execute environment 移除，但这不能
证明所有敏感 host 数据都不可访问。

Shell 边界有意比文件系统工具边界更宽。它能用 Awesome host account 指定敏感或工作区
外路径；环境变量清理并不隔离文件。Handler 会校验 working-directory identity 并重新
运行 policy，但 runner 接收的是 pathname 而不是固定 directory descriptor。同权限进程
仍可在校验与 OS spawn 之间替换该目录。

## 进程生命周期

Core 在异步产品启动前建立进程树所有权。Windows 使用 kill-on-close Job Object；POSIX
使用 session/process group 与 lease supervisor。每个 `execute` 有独立清理域。
Timeout、cancellation、spawn failure、root completion、termination、force-kill 和
stdout/stderr drain 都有边界。

进程所有权解决孤儿清理问题。进程运行时，它不限制文件系统、网络、设备、credential
store 或 child-process 访问。有意 daemonize 的 POSIX 进程可以离开受管 group。

## Web 网络边界

Web 由用户启用且默认关闭。只有有效 `TAVILY_API_KEY` 与 `web.enabled: true` 才会发布
`web_search`；Workspace config 不能启用它或选择 credential。提供商中立 handler 使用一个
可复用 Tavily adapter 和显式 `httpx.AsyncClient`，设置 `trust_env=False`、禁用 redirect、
限制 response，且不进行不透明 retry。环境 proxy 变量会被忽略，只接受经过校验的
`AWESOME_WEB_PROXY_URL` 或其已选择的 Awesome secret。

Destination 固定为 `https://api.tavily.com/search`；query 与 blocked-domain list 会离开
进程发送给 Tavily。启用和首次审批会披露 [Tavily 隐私政策](https://www.tavily.com/privacy)
与[平台条款](https://www.tavily.com/terms)。诊断 allowlist 绝不包含 query、URL、response 或
credential 正文。严格 HTTPS citation 仍是不受信 display data；只有 ID 匹配当前 Turn
经过校验的 source catalog 时才会成为链接。

## 扩展与上下文边界

工作区指令、Skills、Memory、MCP description/result、显式文件和 provider text 都是给
模型的不受信输入。它们不能改变 capability 决策或绕过 Tool Executor。

Workspace Skill discovery/load 会在不跟随链接的情况下重新校验每个路径身份。在一个 server
lock 下，MCP Manager 会编译一个完整、已校验、有界、只含本地引用并包含最终 namespaced
name 的候选项。共享 Registry 会先校验完整聚合快照（128 个工具，以及 1 MiB 规范化模型
定义），再以原子方式替换该服务器 namespace。随后 Manager 在中间没有 `await` 的情况下
发布 generation、client、catalog 和 `CONNECTED`；因此 connected 也能证明 namespace 已
安装。失败会关闭候选 client、使 generation 失效、移除该 namespace，并且只暴露固定、
脱敏的诊断。参数在审批和远程 I/O 前经过 schema 校验。绑定 generation 的 handler 不能
通过旧 validator 调用更新后的 catalog。Timeout 或连接丢失属于不确定结果，不会透明
重放。每次 MCP 调用仍然需要单次审批，即使处于 Full access 也是如此。

一个无效 Skill 或 MCP server 会被隔离为诊断，不会扩大其他扩展的权限。

## Secret 与敏感数据

Provider secret 来自 process environment 或用户通过显式 `/auth` 选择管理的 `.env`。
工作区配置不能提供 secret 值。如果选中来源消失，Awesome 会报告，不会静默 fallback
到其他来源。

Secret 值在配置边界以 secret-aware type 表示，并从受支持的 event/output 路径中脱敏。
工具审计记录参数名称，不记录原始值。直接命令/输出的持久化会经过脱敏与长度限制。

脱敏是纵深防御，而不是 data-loss prevention。获批 shell 命令可以读取用户可访问的 host
数据，提示词也可以要求模型泄露有意提供给它的内容。请避免把 secret 放入工作区或作为
工具参数传递。

### Application 诊断日志边界

Application invocation log 从封闭的字段 allowlist 构造，而不是先序列化任意 request 或
result 再尝试脱敏。每条 record 只包含 `version`、`timestamp`、`session_id`、
`correlation_id`、`operation`、`outcome`、`duration_ms`，以及可选的 `error_code` 与有界
`usage`。Prompt 文本、模型或 Tool 正文、query 文本、URL、文件系统 path、secret、
exception 文本和任意 payload 在构造时即被排除。

进程/会话级 writer 是非阻塞、fail-open 的。其有界 queue 和 5 个文件、每个 5 MiB 的轮转
限制资源使用；诊断 sink 不可用或已满也不能改变被观测的 Application 结果。这些 record 是
运行元数据，不是产品历史，也不能替代 Turn lifecycle 或 audit record。即使 schema 已排除
受支持的内容字段，分享前仍应把这些文件视为本地数据并进行检查。

## 依赖方向

包依赖编码了 authority，但实际 import 契约不是单一的垂直 DAG。Storage 实现多个下层
拥有的 port，Extensions 使用 Context 契约，Safety 使用提供商中立 Modeling type，
Application 是具体 composition root。准确的 importer-to-allowed adjacency 表维护在根
[架构概览](../../ARCHITECTURE.zh-CN.md#文件依赖链)中，并由
`tests/structural/test_dependency_architecture.py` 强制执行；不能把概念数据流图理解成
import permission。

重要框架的所有者也被固定：

| 外部框架 | 允许的所有者 |
| --- | --- |
| `jsonschema` | Extensions/MCP |
| `httpx` | Web/Tavily adapter |
| `mcp` SDK | Extensions |
| `openai` SDK | 具体 Providers |
| `sqlite3` | Storage |
| `langgraph` | Agent、Application invocation 与 Storage checkpoint adapter |

框架所有权可以防止一次便利 import 建立隐藏的第二 provider、database、schema validator
或 graph runtime。

## 生产依赖的理由

Awesome 保持一组精简且显式的生产依赖：

| 依赖 | 所属用途 |
| --- | --- |
| `pydantic` | 跨边界类型化模型与校验；严格程度由具体契约决定 |
| `httpx` | 唯一有界 async Tavily Search client，并显式拥有 proxy 配置 |
| `langgraph`、`langgraph-checkpoint-sqlite` | 唯一 Agent graph 及其 checkpoint saver |
| `openai` | 适配器背后的 DeepSeek/Kimi 兼容 provider client |
| `mcp` | Extensions 背后的 stdio MCP client |
| `jsonschema` | 符合标准的 MCP schema 编译 |
| `PyYAML` | YAML 解析、重复 key 拒绝、配置和 Skill frontmatter |
| `python-dotenv` | 用户 secret 来源加载 |
| 可选 `mem0ai` | 显式启用的 Mem0 Cloud adapter |

新增依赖必须说明其所属 package、现有契约为何不能满足需求、输入/输出如何受限，以及哪些
supply-chain 和 packaging gate 会覆盖它。`tests/structural/test_product_architecture.py`
会锁定 direct dependency inventory。

## 供应链控制

Python 和 npm lockfile 都已提交。Required CI 检查 locked install、wheel/package 内容、
Protocol fixture 和结构所有权。独立 `Security required` workflow 运行 dependency review、
CodeQL、通过 hash-validating PyPI 路径与 OSV lookup 执行的 pip-audit，以及 npm audit。
GitHub Actions 固定到完整 commit hash，Required CI 使用 actionlint 校验 workflow 语法。

GitHub Dependency Graph、Dependabot、ruleset、tag protection、release environment reviewer、
secret scanning 和 push protection 是仓库设置，并非源代码本身可以强制的事实。维护者必须
单独验证这些控制。

## 安全变更检查清单

对于任何新工具、provider、扩展、存储格式或进程路径：

1. 说明受信输入和攻击者可控输入。
2. 识别 capability，并判断 hard denial 是否适用。
3. 在审批或外部 I/O 前校验输入并施加边界。
4. 将审批绑定到当前执行身份。
5. 定义 timeout、cancellation、uncertain-outcome 与 cleanup 行为。
6. 决定哪些内容持久化，以及恢复如何区分 committed 与 uncertain 工作。
7. 证明下层 package 没有导入更高 authority。
8. 添加正常、畸形、边界、竞态、取消和恢复测试。
9. 记录残余风险，不把 policy 称为隔离边界。

## 设计取舍

- Host 执行能与原生开发者工具集成，但把隔离交给外部 sandbox。
- 严格工作区 link/hard-link 拒绝会排除一些合法布局，以换取可解释的 containment。
- Fail-closed parser 与 schema 边界可能拒绝复杂但有效的输入；接受无界或模糊工作会让
  policy 的时机与含义变得不确定。
- 精简的 direct dependency 集中职责，但每个新 provider 或扩展框架都需要显式产品工作。
- 保守恢复可能保留未解决证据并需要用户处理；自动清理可能抹去外部或文件作用的证明。

## 源代码与测试索引

- Trust 与 identity：`core/workspace/`、`storage/trust.py`、
  `application/composition.py`
- 文件系统：`core/filesystem.py`、`core/tools/policy.py`
- 命令 policy/process：`core/tools/command_policy.py`、
  `core/tools/process.py`、`core/process_lifetime.py`
- 权限/interaction：`core/tools/permissions.py`、`application/interactions.py`
- 脱敏与 invocation diagnostics：`safety/redaction.py`、
  `application/diagnostics.py`、`application/middleware.py`
- 依赖测试：`tests/structural/test_dependency_architecture.py`、
  `tests/structural/test_product_architecture.py`
- 安全敏感测试：`tests/unit/core/`、`tests/unit/extensions/`、
  `tests/integration/test_workspace_trust.py`、
  `tests/integration/test_state_reset_concurrency.py`
