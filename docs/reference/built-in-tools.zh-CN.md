# 内置工具参考

工具是模型意图通往 Workspace 读取、文件修改或宿主执行的唯一途径。模型提交名称和 JSON
参数；Core 验证已注册 schema、评估 capability policy、按需获得审批、在期限内调用 handler、
记录一项终态 activity，并发出一个终态 tool event。

```text
Model tool call
       |
       v
registered model validation -> lexical hard checks -> permission policy
                                              |          |
                                            allow       ask
                                              |          |
                                              |   bound user interaction
                                              |          |
                                              +----+-----+ approved
                                                   |
                                                   v
                                      handler safety -> deadline
                                                   |
                                                   v
                                      Change Journal / audit
                                                   |
                                                   v
                                  bounded result + terminal event
```

Runtime registry 始终包含四个 Workspace 读取工具和两个 Skill 支持 registration。每个
Turn 的模型可见 catalog 会按冻结模式过滤 Skill 工具：`auto` 包含两个，`off` 均不包含，
具名 Skill 只包含 `read_skill_resource`。正常本地 composition 还包含文件修改和 shell 工具。
只有启用 Local Memory 时才会出现 Local Memory 工具，有效 MCP catalog 会添加
namespaced tool。动态 MCP 行为在 [MCP](../extensions/mcp.zh-CN.md)中单独说明。只有 user Web
config 已启用且存在有效 `TAVILY_API_KEY` 时，才会出现 `web_search` 与 `web_fetch`。

## 通用请求与结果契约

请求包含唯一 `call_id`、已注册的 `tool_name` 和参数 object。未知名称返回 `not_found`；
schema 不匹配返回通用 `invalid_arguments` 错误，不会回显敏感参数或 schema。

Awesome 自有的内置、Local Memory 和 Skill 支持参数 model 构成严格、封闭的 JSON 契约。
未知 object 字段和标量强制转换都会被拒绝，因此调用方必须只使用文档字段和原生 JSON
类型；例如，`5` 是整数，而 `"5"` 不是。MCP 工具保持动态，并遵循各 server 经过有界编译的
JSON Schema，而不是这一静态 model 基类。

结果包含：

- 匹配的 `call_id` 和 `tool_name`；
- `status`，取值为 `success` 或 `error`；
- 最多 30,000 个字符、模型可见的 `content`；
- 有界结构化 `metadata`；
- 有序且严格的 `Citation(id, title, url)` tuple，通常为空；
- 仅错误结果包含的 error code/message；
- TUI 使用的展示字段：verb、target、outcome、summary、有界 detail、truncation count 和
  duration。

稳定的内置 error code 为 `invalid_arguments`、`not_found`、`workspace_not_trusted`、
`workspace_escape`、`permission_denied`、`conflict`、`timeout`、`state_unavailable`、
`execution_failed`、`uncertain_outcome`、`memory_disabled`、`memory_conflict`、
`memory_rejected` 和 `cancelled`。Web 工具还使用 `web_request_rejected`、
`web_request_budget_exhausted`、`web_credential_rejected`、`web_rate_limited`、
`web_quota_exhausted`、`web_provider_unavailable`、`web_timeout`、
`web_connection_failed` 与 `web_malformed_response`。当另一进程持有 Local Memory mutation 锁时，
`timeout` 可重试；`state_unavailable` 表示无法安全使用 lock sidecar 或平台锁边界，
不可重试。`uncertain_outcome` 主要用于 MCP 边界：它表示外部副作用可能已经发生，
绝不能自动重放。

普通 handler 的外层期限是 30 秒。`execute` 提供下面说明的动态期限；两个 Web 工具都使用
20 秒 tool deadline，内部 HTTP client timeout 为 15 秒。有界清理后会继续传播取消；取消
不会转换为普通错误结果。

## Workspace 路径规则

结构化 filesystem-tool 的 `path` 参数以及 `execute` 的 working directory 都相对于
Workspace。绝对路径、`..` 逃逸和有歧义的 host 语法会被拒绝。Filesystem 读取和修改会
固定并重新检查 workspace/path 身份，因此之后的目录交换会失败，而不是静默重定向预期的
文件操作。

Filesystem 工具绝不*跟随* symlink、junction 或其他 reparse point。只有一个有意的区别：
在平台能够安全处理的情况下，`delete` 可以删除最终 symlink 节点本身，而不跟随其目标。
父组成部分中的链接、递归 inventory 中的嵌套链接，以及 Windows directory reparse target
都会在删除开始前遭到拒绝。

内置 **filesystem** 工具不能访问敏感路径。这包括 `.env` 及其非示例变体、`.ssh`、常见
private-key 后缀/名称、credential 或 secret 路径组成部分，以及 AWS credential 文件。
目录 listing 还会隐藏 `.git`；删除始终保护 `.git` 和敏感路径。

这些检查不会约束已获审批的 host shell 能够点名的内容。`execute` 可以使用 Awesome 进程的
host account 读取敏感文件或 Workspace 外文件，尤其是在 Full access 下。清除常见秘密环境
变量名能减少继承，却不提供 filesystem isolation。Working-directory handler 会解析身份，
并在调用 runner 前立即重新运行 command circuit breaker，但它传给 OS spawn API 的是路径名，
而不是已固定的 directory handle；同权限并发替换仍是一条 TOCTOU 边界。见
[权限模式](permission-modes.zh-CN.md)。

## 读取工具

### `glob`

使用相对 glob 查找普通 Workspace 文件。

| 参数 | 类型 | 默认值 | 限制/语义 |
| --- | --- | --- | --- |
| `pattern` | string | 必需 | 1–500 个字符；不能是绝对路径或包含 `..` |
| `path` | string | `.` | 用作搜索根的现有目录 |
| `max_results` | integer | 200 | 1–1,000 |

结果 content 每行一个相对路径。Metadata 包含路径数组和 `truncated`。枚举会剪枝 `.git`、
Python/pytest/mypy/Ruff cache、`.venv`、`venv`、`build`、`dist` 和 `node_modules`。

```json
{"pattern":"tests/**/*.py","path":".","max_results":250}
```

### `grep`

逐行搜索 UTF-8 文本文件。

它使用与 `glob` 相同的文件枚举器和默认剪枝目录集合。

| 参数 | 类型 | 默认值 | 限制/语义 |
| --- | --- | --- | --- |
| `pattern` | string | 必需 | 1–1,000 个字符 |
| `path` | string | `.` | 现有搜索根目录 |
| `include` | string 或省略 | 省略 | 可选相对文件 glob，最多 500 个字符 |
| `regex` | boolean | `true` | 为 true 时把 `pattern` 当作 Python 正则表达式 |
| `case_sensitive` | boolean | `true` | 控制 regex flag 或 literal comparison |
| `max_results` | integer | 100 | 1–500 个匹配行 |

每行渲染为 `path:line: text`；单个匹配行限制为 2,000 个字符。二进制、非 UTF-8 以及大于
1 MiB 的文件会跳过。Metadata 包含结构化 path/line/text 记录和 `truncated`。

```json
{"pattern":"ForegroundArbiter","path":"src","include":"**/*.py"}
```

### `ls`

非递归地列出一个目录。

| 参数 | 类型 | 默认值 | 限制/语义 |
| --- | --- | --- | --- |
| `path` | string | `.` | 现有目录 |
| `max_entries` | integer | 200 | 1–1,000 |

Content 包含 `type<TAB>path`。Metadata 包含 `{name, path, type}` 条目和 `truncated`。
Listing 绑定身份，并排除受保护条目。

### `read_file`

从一个 UTF-8 普通文件读取有界行范围。

| 参数 | 类型 | 默认值 | 限制/语义 |
| --- | --- | --- | --- |
| `path` | string | 必需 | 现有普通文件，最多 1 MiB |
| `start_line` | integer | 1 | 至少为 1 |
| `end_line` | integer 或省略 | 文件末尾 | 至少为 1 |

最多返回 500 行和 30,000 个渲染字符。行带有从 1 开始的前缀，例如 `17: content`。
Metadata 报告请求/实际范围、总行数和 truncation。包含 NUL、非 UTF-8、超大或身份变化的
文件会失败，而不是启发式解码。

## 文件修改工具

每次修改都在打开的 ChangeSet 中运行。Before/after byte、mode 和 identity 通过 Change
Journal 捕获，因此 `/diff`、`/undo`、crash reconciliation 和审计看到的是同一个修改边界。
一个 ChangeSet 最多可包含 1,000 个 filesystem node 和 50 MiB 已捕获内容。

`write_file`、`edit_file` 和 `delete` 都是 `non_replayable`。Journal 支持的可逆性不代表
调用重放具有幂等性：崩溃后，用户或其它进程可能已经修改或重新创建同一路径。因此，这些
调用的结果不确定时，恢复默认选择 Abort。

### `write_file`

创建或原子替换 UTF-8 文件。

| 参数 | 类型 | 限制 |
| --- | --- | --- |
| `path` | string | 相对于 Workspace 的目标 |
| `content` | string | 最多 1,000,000 个字符 |

如果目标存在，它必须是普通文件，并且当前内容必须符合 ChangeSet byte limit。原 mode 会保留。
父组成部分必须已经存在；绝不会遍历链接。

```json
{"path":"notes/review.md","content":"# Review\n\nReady.\n"}
```

### `edit_file`

应用精确的文本替换，并保留文件 mode。

| 参数 | 类型 | 默认值 | 限制/语义 |
| --- | --- | --- | --- |
| `path` | string | 必需 | 现有 UTF-8 普通文件，最多 1 MiB |
| `old_string` | string | 必需 | 1–200,000 个字符 |
| `new_string` | string | 必需 | 0–200,000 个字符 |
| `replace_all` | boolean | `false` | 为 true 时替换每个精确匹配项 |

零个匹配返回 `not_found`。`replace_all: false` 时多个匹配返回 `conflict`；工具不会猜测模型
指的是哪一次出现。

### `delete`

删除一个文件，或递归删除一个目录。

| 参数 | 类型 | 限制/语义 |
| --- | --- | --- |
| `path` | string | Workspace 中现有的非 root 路径 |

在首次移除前，Core 会清点完整 subtree，并针对受保护路径、link/reparse 规则、identity、
1,000-node limit 和 50 MiB capture limit 验证每个节点。任何嵌套 junction/reparse directory
都会使 inventory 以**零删除**失败。任何权限模式都不能删除 Workspace root、filesystem root、
`.git` 和敏感目标。

删除是一项独立的 `workspace.delete` capability。因此 Accept edits 不会从“编辑文件”静默扩大
为“移除一棵目录树”。

## Shell 执行：`execute`

从经过验证的 Workspace 目录运行 host-shell 命令。

| 参数 | 类型 | 默认值 | 限制/语义 |
| --- | --- | --- | --- |
| `command` | string | 必需 | 1–8,000 个字符 |
| `cwd` | string | `.` | 现有 Workspace 目录；不能是链接 |
| `timeout_seconds` | number | 60 | 大于 0，最多 600 |
| `max_output_chars` | integer | 30,000 | 有界 runner capture 单独使用的 1,000–200,000 |

Windows 上 runtime 调用 `cmd.exe /d /s /c`；POSIX 上调用 `/bin/sh -lc`。Command policy 仍能
理解 CMD、POSIX shell 和 PowerShell payload，因此会递归检查已知 wrapper。审批前和进程
启动前会评估同一个纯 policy。

Policy 会规范化 executable path、大小写和 Windows executable suffix；拆分 compound
command、pipe 和 newline；跟踪已知 shell/wrapper payload；解码 PowerShell
`EncodedCommand`；并检查 literal Python `-c` 调用，例如 `os.system`、`subprocess.*` 和
`shutil.rmtree`。检查限制为八层 wrapper 和 64 个 command node。无法安全分类的模糊控制流
或动态 executable expansion 会被拒绝。

不可关闭的 circuit breaker 会拒绝：

- 对 filesystem root 或 Workspace root 的递归/破坏性删除；
- host shutdown/reboot 和 privilege elevation；
- disk formatting/partition command 和裸 block-device 写入；
- 可识别的 shell fork bomb；
- 不安全或过度复杂的 wrapper 形式。

这被刻意设计为事故预防，而不是任意恶意 shell 文本安全性的证明。命令可以引用另一个绝对
路径，并仍然进入正常审批决定；Full access 随后可能允许它。不受信任代码需要更强隔离时，
请使用操作系统 containment。

启动前，会从子进程环境中移除名称以 `_API_KEY`、`_TOKEN`、`_SECRET` 或 `PASSWORD` 结尾的
变量。输出在到达模型或 TUI 前会再次脱敏。

Runner 对 spawn、process wait、process-tree termination、graceful/force kill wait、Windows
`taskkill` 和 stdout/stderr drain 都设置边界。语义 command deadline 是 `timeout_seconds`；
Tool Executor 总外层期限是该值加十秒清理预算。因此，有效的 45 秒命令不会被普通 30 秒工具
限制截断。内层 timeout 会报告带执行 metadata 的 `timeout`；外层期限只是后端未履行契约时
的最后保障。

Execute observation 会在 runner 启动前立即记录。参数错误、policy hard-denial 和权限拒绝
不会生成 observation；spawn/backend failure、timeout 和取消则会保守记录不可逆尝试可能
已经开始。每次调用仍然最多生成一个 terminal tool event 和一条 ToolActivity。

## 公共 Web 工具

Web 默认关闭。配置 `/auth tavily`、保持 provider 为 `tavily`，再运行 `/web on`；
Workspace config 可以降低每 Turn budget 或添加 blocked domain，但不能启用 Web 或选择凭据。

### `web_search`

该工具向 Tavily 提交 basic Search：

```text
POST https://api.tavily.com/search
```

| 参数 | 类型 | 默认值 | 限制/语义 |
| --- | --- | --- | --- |
| `query` | string | 必填 | Trim 后非空，1–2,000 个字符；拒绝控制分隔符 |
| `max_results` | integer | `5` | 1–10；Tavily `search_depth` 始终为 `basic` |

配置的 `blocked_domains` 会进入 Tavily exclusion list。Awesome 不请求 generated answer、
raw content、image 或 favicon。Response 限制为 1 MiB 和最多十条严格 HTTPS result；模型可见
JSON 限制为 28,000 个字符。不跟随 redirect，也没有不透明 automatic retry。HTTP 429、5xx、
timeout、连接失败、凭据失败、用量限制与 malformed body 都映射成上文稳定且脱敏的 error code。

### `web_fetch`

该工具要求 Tavily 云服务从一个 URL 提取可读内容：

```text
POST https://api.tavily.com/extract
```

| 参数 | 类型 | 默认值 | 限制/语义 |
| --- | --- | --- | --- |
| `url` | string | 必填 | 一个绝对公共 HTTPS URL，最多 8,000 个字符；不得包含用户信息、fragment、特殊用途/私有 host，path 也不能指向 PDF 或其他已识别二进制格式 |

Awesome 把该 URL 发送给 Tavily，并选择 basic Markdown extraction。由 Tavily 而不是
Awesome Core 连接目标网站。规范化 response 包含一个严格的公共 HTTPS URL 和最多 24,000 个
字符的提取正文。工具返回包含 `source_id`、`url`、`content`、`truncated` 的 JSON，以及
`content_chars`、`truncated` metadata 和一条标题为
`Fetched content from <lowercase-hostname>` 的 citation。配置的
`blocked_domains` 会在审批前拒绝完全匹配的 URL hostname 及其子域。

它有意不成为浏览器或通用下载器：不提供 Cookie、登录、JavaScript、PDF、任意二进制、
本地 Fetch、持久缓存或 backend fallback。Awesome 不会在本机跟随目标站 redirect，也不会
静默重试结果不确定的请求。

### 共享网络、权限与引用契约

可复用 async HTTP client 设置 `trust_env=False`，使用 Awesome 显式 User-Agent，并忽略环境
proxy 变量。可选代理只能通过 `AWESOME_WEB_PROXY_URL`（或对应 Awesome secret）配置；只接受
不嵌入凭据的 `http`/`https` proxy URL。

`network.read` 在每种 permission mode 下首次使用都会 ASK。用户可以选择默认 deny、allow
once 或当前 Thread allow。审批完成后，任一工具才会消耗同一份冻结 `web_requests` budget
的一个单位；Search 与 Fetch 共享默认值/硬上限均为每 Turn 八次的预算，Workspace config
只能降低它。切换 Thread、重建 runtime、更改 permission mode、运行 `/web revoke` 或
`/web off`，以及退出时都会清除 Thread grant。两个工具都是 `non_replayable`，因此不确定
崩溃后的 recovery 默认 Abort。

每条 Search result 和 Fetch response 都会获得稳定的 Turn-local source ID（`S1`、`S2`……），
并按 URL 去重。模型使用 `[[S1]]` 引用。未知 ID 只显示为文本而不生成链接，并产生 warning。
Web 返回来源但最终回答没有使用任何来源时，finalization 会附加有界 Sources 区域并发出
warning。同一 citations 会贯穿 ToolResult、Agent state/checkpoint、Conversation、Protocol
v5、TUI 与 headless JSON v2。

Search query 或请求的 Fetch URL 会发送给 Tavily，并依据
[Tavily 隐私政策](https://www.tavily.com/privacy)与
[Tavily 平台条款](https://www.tavily.com/terms)处理。结构化诊断不会记录 query、result URL、
result body 或凭据。

## Skill 支持工具

这些只读工具使用 `context.read`。它们保留在唯一的 Runtime registry 中，而冻结的 Turn
mode 控制模型可见性与硬准入。

| 工具 | 参数 | 结果 |
| --- | --- | --- |
| `load_skill` | `name`：小写连字符名称，最多 64 个字符 | 有界 Skill 正文，以及 source、truncation 和说明性 `allowed_tools` metadata |
| `read_skill_resource` | `name`；1–2,000 个字符的 `relative_path` | 一个有界文本资源，最多 5,000 个估算 token |

`auto` 对冻结 catalog 中的 identity 暴露两个工具；具名模式只对该 identity 暴露
`read_skill_resource`；`off` 两者都不暴露。每次调用都会重新检查发现时的 package identity，
因此 Runtime 重建或包漂移会返回 `conflict`，而不会扩大访问。详情见
[Skills](../extensions/skills.zh-CN.md)。

## Local Memory 工具

这些工具仅在 `memory.local_file_memory` 为 true 时出现。其自定义 `memory.read`/
`memory.write` capability 服从 Memory policy；它们不使用三行 Workspace 权限，也不弹出
prompt。修改型工具 description 告诉模型，只有当前用户明确请求时才可调用，但 runtime 不会
对对话进行语义分类以证明该请求。Runtime 强制执行的是匹配的受信任 Workspace、Agent 来源、
活动 Turn、有效内容和最后观察到的 compare-and-swap hash。

| 工具 | 参数 |
| --- | --- |
| `memory_list` | `scope`：`user` 或 `workspace` |
| `memory_add` | `scope`；`content`（1–2,000 个字符）；`expected_hash`（64 个小写 hex） |
| `memory_replace` | add 字段，加上匹配 `memory_` + 32 个小写 hex 的 `entry_id` |
| `memory_remove` | `scope`；`entry_id`；`expected_hash` |

Hash 是 compare-and-swap 状态。发生 `memory_conflict` 时，请重新 list 并作出新决定；自动
盲目重试可能覆盖并发编辑。见 [Memory](../extensions/memory.zh-CN.md)。
