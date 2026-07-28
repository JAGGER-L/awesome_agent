# Awesome

[English](README.md) | [简体中文](README.zh-CN.md)

```text
  ███  █   █ █████ █████  ███  █   █ █████
 █   █ █   █ █     █     █   █ ██ ██ █
 █████ █ █ █ ████  █████ █   █ █ █ █ ████
 █   █ ██ ██ █         █ █   █ █   █ █
 █   █ █   █ █████ █████  ███  █   █ █████
```

Awesome 是一个运行在终端中的 AI 编程助手。它能够理解代码库、修改文件、执行
命令，并协助你完成开发、调试、重构和测试。

在项目目录中启动 `awesome`，用自然语言描述你的目标。Awesome 会阅读相关代码、
执行必要的工具、完成修改，并协助验证结果。

## Awesome 能做什么

- 理解项目结构并解释代码之间的关系；
- 实现功能、调试问题、重构代码和运行测试；
- 通过 `/diff`、`/undo`、`/redo` 检查和撤销受控文件修改；
- 搜索当前 Workspace 的会话历史，并恢复匹配结果；
- 在终态 Turn 处分叉会话，或在新的分支 Thread 中重试该 Turn；
- 通过 Change Journal 将当前 Thread 确定性导出为 Markdown 或 JSON；
- 继续最近的 Thread，或通过 ID 恢复指定 Thread；
- 以确定性的文本或 JSON 输出非交互运行一个 Agent Turn；
- 在 Request approval、Accept edits 和 Thread 范围的 Full access 之间切换；
- 使用 Skills、MCP 工具、本地 Memory 和 Mem0 Cloud 扩展能力；
- 无需打开交互式聊天即可列出、安装、替换和移除经过校验的本地 User Skill 包；
- 通过可选且带引用的 Tavily 集成搜索和提取公共 Web 内容；
- 使用 DeepSeek 和 Kimi 模型。

Awesome 最开始提供 `ls`、`read_file`、`write_file`、`edit_file`、`delete`、
`glob`、`grep` 和 `execute`。扩展可以继续增加工具，Awesome 不限制为八个工具。
Local memory 与 Mem0 Cloud 相互独立，二者默认关闭。

Web 工具同样默认关闭。通过遮罩的 `/auth tavily` 配置 Tavily，或选择已有的
`TAVILY_API_KEY`，再通过 `/web on` 启用，并批准每个 Thread 第一次 `network.read` 请求。
Search query 与 Fetch URL 会依据 Tavily 的
[隐私政策](https://www.tavily.com/privacy)和[平台条款](https://www.tavily.com/terms)
发送给 Tavily；Awesome 会分配稳定的 `S1...` 来源，并将其带入最终回答。使用 `/web off`
可关闭集成，使用 `/web revoke` 可清除当前 Thread grant。

## 安装

### Apple Silicon macOS 或 WSL2 Ubuntu 24.04 x64

```bash
curl -fsSL https://github.com/JAGGER-L/awesome_agent/releases/latest/download/install.sh | sh
```

### Windows 11 x64

```powershell
irm https://github.com/JAGGER-L/awesome_agent/releases/latest/download/install.ps1 | iex
```

安装后请打开一个新终端。Awesome 已包含运行所需的环境，无需提前安装 Python、
Node.js、uv 或 npm。Git 是可选能力，Awesome 不会自动安装；需要 Git 工作流时，
请从 [Git 官方网站](https://git-scm.com/downloads)安装。

## 开始使用

在项目目录中启动 Awesome：

```text
cd <project>
awesome
```

首次进入一个目录时，Awesome 会显示完整路径并询问是否信任。只有在你了解该项目、
并愿意让 Awesome 读取和操作其中内容时才选择 Yes。Awesome 默认使用 Request
approval 模式；可通过 `/permissions` 查看或切换当前 Thread 的权限模式。

信任后，Awesome 会把根目录中普通的 `AGENTS.md` 读取一次，作为本次会话的项目
指令快照。使用链接、非 UTF-8、二进制、读取时变化或超限的文件会被整份忽略，
并在 Welcome、状态栏和 `/doctor` 中持续显示警告。

尚未配置模型 Provider 时，按 Enter 或运行 `/model`。选择 DeepSeek 或 Kimi，
在遮罩输入框中粘贴 API Key，再选择模型。之后可使用 `/auth` 添加、替换或删除凭据。

常用启动参数：

```text
awesome --continue
awesome --resume
awesome --resume <thread_id>
awesome run "Analyze the test failure" --trust-workspace
awesome skills list
awesome skills install ./review-api
awesome skills remove review-api --yes
awesome --version
awesome --help
```

`awesome run "<prompt>"` 是供脚本使用的非交互入口。它默认创建新 Thread，只把最终
回答写入 stdout（`--format text` 或 `--format json`），诊断写入 stderr。使用
`--thread <id>` 可指定现有 Thread。信任、权限检查、取消以及同一套私有 Core/Application
生命周期仍然生效；存在未解决 interaction 时会退出，不会打印部分回答。完整选项和退出码
见 [CLI 参考](docs/reference/cli.zh-CN.md)。

`awesome skills` 是不进入聊天界面的独立 User Skills 包管理入口。可以从本地包目录或 ZIP 安装；
添加 `--replace` 可替换已有包，无人值守移除则使用 `remove --yes`。未提供 `--yes` 时，
只有交互式终端会执行确认。官方包命令是一次性的，并会关闭其私有 Core。已经初始化的
Awesome Session 会保留不可变 Skill catalog，因此选择或加载变更后的包之前应重启该 Session。
完整包格式和安全契约见 [Skills](docs/extensions/skills.zh-CN.md)。

如果启动时发现未完成的 Turn，Awesome 会先询问再继续。只有待处理工具可安全重复时，
已验证的本地 checkpoint 才默认提供 Retry；如果文件修改、shell、MCP 或 Web 调用结果
不确定，则默认提供 Abort，并且绝不会自动重放该操作。

`/fork [turn_id]` 会把截至某个终态 Turn 的会话历史物化到新 Thread；
`/retry [turn_id]` 会物化该 Turn 之前的前缀，再使用原 Turn 冻结的模型、Thinking、
Skill 与预算重新执行其请求。两者都会创建独立记录，而不是共享历史 DAG；不会复制
checkpoint、Tool activity 或 ChangeSet。retry 也不会重放旧工具调用或撤销既有副作用。

## 第一个任务

可以先让 Awesome 只阅读并介绍项目：

```text
分析这个项目的结构，并告诉我应该从哪里开始阅读。
```

## 文档

- [浏览文档网站](https://jagger-l.github.io/awesome_agent/zh-cn/)
- [文档总览](docs/README.zh-CN.md)
- [安装并完成快速开始](docs/getting-started/quickstart.zh-CN.md)
- [建立日常工作流](docs/user-guide/README.zh-CN.md)
- [理解权限与安全修改](docs/user-guide/permissions.zh-CN.md)
- [选择 Memory、Skills 或 MCP](docs/extensions/README.zh-CN.md)
- [查询命令、配置、工具和协议的精确契约](docs/reference/README.zh-CN.md)
- [架构](ARCHITECTURE.zh-CN.md)
- [贡献与开发](docs/development/README.zh-CN.md)
- [故障排查](docs/user-guide/troubleshooting.zh-CN.md)
- [Roadmap](docs/roadmap.zh-CN.md)

开发者可以通过 `uv run awesome-dev` 运行当前源码；完整的环境准备、启动和故障排查流程请参阅
[开发环境](docs/development/setup.zh-CN.md)。

## 安全

请通过[安全策略](https://github.com/JAGGER-L/awesome_agent/blob/main/SECURITY.zh-CN.md)
([English](https://github.com/JAGGER-L/awesome_agent/blob/main/SECURITY.md))
私密报告漏洞；不要在公开 issue 中披露敏感细节。

只信任你了解的项目，保留修改前先检查 `/diff`。只通过 Awesome 的
`/model` 或 `/auth` 遮罩输入流程输入凭据。Full access 仅对当前 Thread 有效，
只提升内置本地能力，且不会绕过硬性安全拒绝；MCP 和未知扩展能力仍会逐次询问。
任何权限模式都不提供操作系统沙箱，命令 circuit breaker 只用于拦截可识别的误操作，
不能识别任意恶意混淆。受控的 Workspace 文件操作会绑定已检查的目录与文件身份，
且不会沿链接或 reparse point 访问外部目标。递归清单会拒绝嵌套 reparse 目录，
修改操作会拒绝有歧义或 hard-link 别名；有界的进程树清理会减少遗留子进程，
但不会隔离宿主机执行。进程环境变量和
`<AWESOME_HOME>/.env` 仍是高级配置方式；不要把凭据写入项目文件。
可选 Web Search 会把 query 发送给 Tavily。`web_fetch` 会把一个公共 HTTPS URL 发送给
Tavily，由 Tavily 云服务远程获取并提取页面；Awesome Core 本身不连接目标网站。两个工具都
不会继承环境中的代理变量，也不会在结构化诊断中记录 query、URL 或结果正文；只有明确需要
代理时才使用 `AWESOME_WEB_PROXY_URL`。Web Fetch 不是浏览器，不支持 Cookie、登录、
JavaScript、PDF、二进制、本地 Fetch、缓存或 backend fallback。
