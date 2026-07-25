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
执行必要的工具、完成修改并验证结果。

## Awesome 能做什么

- 理解项目结构并解释代码之间的关系；
- 实现功能、调试问题、重构代码和运行测试；
- 通过 `/diff`、`/undo`、`/redo` 检查和撤销受控文件修改；
- 继续最近的 Thread，或通过 ID 恢复指定 Thread；
- 在 Request approval、Accept edits 和 Thread 范围的 Full access 之间切换；
- 使用 Skills、MCP 工具、本地 Memory 和 Mem0 Cloud 扩展能力；
- 使用 DeepSeek 和 Kimi 模型。

Awesome 最开始提供 `ls`、`read_file`、`write_file`、`edit_file`、`delete`、
`glob`、`grep` 和 `execute`。扩展可以继续增加工具，Awesome 不限制为八个工具。
Local memory 与 Mem0 Cloud 相互独立，二者默认关闭。

## 安装

### macOS 或 WSL2 Ubuntu

```bash
curl -fsSL https://github.com/JAGGER-L/awesome_agent/releases/latest/download/install.sh | sh
```

### Windows

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
awesome --version
awesome --help
```

如果启动时发现未完成的 Turn，Awesome 会先询问再继续。已验证的本地 checkpoint
默认提供 Retry；如果 shell 或 MCP 调用结果不确定，则默认提供 Abort，并且绝不会
自动重放该外部操作。

## 第一个任务

可以先让 Awesome 只阅读并介绍项目：

```text
分析这个项目的结构，并告诉我应该从哪里开始阅读。
```

## 文档

- [快速开始](docs/getting-started/quickstart.zh-CN.md)
- [命令](docs/user-guide/commands.md)
- [配置](docs/user-guide/configuration.md)
- [Workspace 与工具](docs/user-guide/workspace-and-tools.md)
- [Memory、Skills 与 MCP](docs/user-guide/memory-skills-mcp.md)
- [故障排查](docs/user-guide/troubleshooting.md)
- [架构](ARCHITECTURE.md)
- [开发](docs/development/README.md)
- [Roadmap](docs/roadmap.md)

开发者可以通过 `uv run awesome-dev` 运行当前源码；完整的环境准备、启动和故障排查流程请参阅
[从源码启动](docs/getting-started/quickstart.zh-CN.md#从源码启动)。

## 安全

只信任你了解的项目，保留修改前先检查 `/diff`。只通过 Awesome 的
`/model` 或 `/auth` 遮罩输入流程输入凭据。Full access 仅对当前 Thread 有效，
只提升内置本地能力，且不会绕过硬性安全拒绝；MCP 和未知扩展能力仍会逐次询问。
任何权限模式都不提供操作系统沙箱，命令 circuit breaker 只用于拦截可识别的误操作，
不能识别任意恶意混淆。受控的 Workspace 文件操作会绑定已检查的目录与文件身份，
并拒绝链接、reparse point、hard-link 别名和有歧义的 Windows 路径写法；有界的
进程树清理会减少遗留子进程，但不会隔离宿主机执行。进程环境变量和
`<AWESOME_HOME>/.env` 仍是高级配置方式；不要把凭据写入项目文件。
