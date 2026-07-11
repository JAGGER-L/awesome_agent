# Awesome Agent

[English](README.md) | [简体中文](README.zh-CN.md)

```text
  ███  █   █ █████ █████  ███  █   █ █████
 █   █ █   █ █     █     █   █ ██ ██ █
 █████ █ █ █ ████  █████ █   █ █ █ █ ████
 █   █ ██ ██ █         █ █   █ █   █ █
 █   █ █   █ █████ █████  ███  █   █ █████
```

Awesome 是一个在启动目录内工作的 Local-first AI Coding Agent；它不是托管
服务，也不是通用 Agent Platform。

V1.0.0 是面向少量用户的有限试用版本。支持 Apple Silicon macOS、Windows 11
x64 和 WSL2 Ubuntu 24.04 x64。

## 安装

Apple Silicon macOS 或 WSL2 Ubuntu 24.04 x64：

```bash
curl -fsSL https://github.com/JAGGER-L/awesome_agent/releases/latest/download/install.sh | sh
```

Windows 11 x64 PowerShell：

```powershell
irm https://github.com/JAGGER-L/awesome_agent/releases/latest/download/install.ps1 | iex
```

安装后请打开新终端。用户无需预装 Python、Node.js、uv、npm、Docker 或 Make。
Git 是可选能力，Awesome 不会安装 Git；如需 Git 工作流，请从
[Git 官方网站](https://git-scm.com/downloads)安装。

## 首次运行

```text
cd <workspace>
awesome
```

启动目录就是 workspace。读取项目配置、指令、Skills、MCP 声明或运行工具前，
Awesome 会请求 trust；拒绝后直接退出，也不会记录该目录为可信。

至少在 `<AWESOME_HOME>/.env` 中配置一个模型密钥：

```dotenv
DEEPSEEK_API_KEY=...
# 或
MOONSHOT_API_KEY=...
```

V1 仅正式支持 DeepSeek 和 Kimi。模型选择与第一个安全任务见
[快速开始](docs/getting-started/quickstart.zh-CN.md)。

## 核心能力

最初的默认工具是 `ls`、`read_file`、`write_file`、`edit_file`、`delete`、
`glob`、`grep` 和 `execute`。MCP 与用户工具可以继续扩展，架构不限制为八个
工具。文件修改进入 Change Journal，供 `/diff`、`/undo`、`/redo` 使用。

本地 `USER.md`/workspace `MEMORY.md` 与 Mem0 Cloud 是相互独立的两层记忆，
二者默认关闭。Skills 提供任务指令，MCP 连接外部工具；它们都不能绕过
workspace trust 或工具策略。

## 启动参数

```text
awesome
awesome --continue
awesome --resume
awesome --resume <thread_id>
awesome --version
awesome --help
```

## 文档

- [快速开始](docs/getting-started/quickstart.zh-CN.md)
- [架构](ARCHITECTURE.md)
- [开发](docs/development/README.md)

## 安全

Awesome 当前直接在本地主机运行工具，没有 Docker sandbox。只信任你了解的
workspace，检查 diff，并把密钥放在操作系统环境或 `<AWESOME_HOME>/.env`，
不要写入项目文件。
