# Awesome Agent

[English](README.md) | [简体中文](README.zh-CN.md)
```text
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃  ███  █   █ █████ █████  ███  █   █ █████        ┃
┃ █   █ █   █ █     █     █   █ ██ ██ █            ┃
┃ █████ █ █ █ ████  █████ █   █ █ █ █ ████         ┃
┃ █   █ ██ ██ █         █ █   █ █   █ █            ┃
┃ █   █ █   █ █████ █████  ███  █   █ █████        ┃
┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛
```
Awesome Agent 是一个面向本地项目的 AI coding agent，定位是运行在开发者
电脑上的轻量级本地开发助手。它可以读取代码库上下文、修改文件、执行命令，并
辅助调试、重构和功能实现。相比传统代码补全工具，Awesome 更偏向任务级开发：
用户描述目标后，它会围绕当前仓库状态进行多轮推理、编辑和验证，适合终端优先、
自动化程度较高的工程工作流。当前主要使用入口是本地 CLI；如需连接其它客户端，
也可以选择 Local API 或 Docker API 模式。


## 选择使用方式

| 方式 | 适合场景 | 启动命令 |
| --- | --- | --- |
| Local CLI | 你想直接在本地项目里用终端工作。它使用 embedded local runtime，不需要 API server。 | `cd <your-project>` 然后运行 `awesome` |
| Local API | 你想在本机提供 API 地址或查看接口文档。目前只支持 Windows。 | `make dev` |
| Docker API | 你想通过 Docker 运行 API。目前只支持 Windows。 | `make docker-start` |

大多数用户建议从 **Local CLI** 开始。API 方式适合需要把其它客户端连接到
Awesome 的场景。如需把 TUI 连接到已有 API，而不是使用 embedded local runtime，
可以运行 `awesome --api-url <url>`。

从项目目录运行 `awesome`。启动目录会成为默认 thread context。如果它是一个
Git checkout，runs 会继承该 repository；否则 Awesome 会使用 workspace-only
mode，并且仍然接受用户消息 turn。普通用户消息是唯一的产品执行创建路径。

## 快速开始

克隆并安装 Awesome：

Windows PowerShell：

```powershell
git clone https://github.com/JAGGER-L/awesome_agent.git
cd awesome_agent
make install
```

macOS/Linux：

```bash
git clone https://github.com/JAGGER-L/awesome_agent.git
cd awesome_agent
make install
```

创建 Awesome 用户目录：

Windows PowerShell：

```powershell
awesome init
```

macOS/Linux：

```bash
awesome init
```

把模型 key 设置到操作系统环境变量，或写入 `<AWESOME_HOME>/.env`：

Windows PowerShell：

```powershell
setx AWESOME_AGENT_DEEPSEEK_API_KEY "your-key"
```

macOS/Linux：

```bash
mkdir -p "${AWESOME_HOME:-$HOME/.awesome-agent}"
printf 'AWESOME_AGENT_DEEPSEEK_API_KEY=your-key\n' >> "${AWESOME_HOME:-$HOME/.awesome-agent}/.env"
```

然后进入你的项目并启动 Awesome：

Windows PowerShell：

```powershell
cd E:\my-project
awesome
```

macOS/Linux：

```bash
cd ~/my-project
awesome
```

发送一条普通消息，例如：

```text
Read this project and explain how it is organized.
```

完整步骤见 [Quickstart](docs/getting-started/quickstart.md) 或
[快速开始](docs/getting-started/quickstart.zh-CN.md)。

## 配置基础

Awesome 会把自己的用户文件放在项目目录之外。

| 路径 | 作用 |
| --- | --- |
| `<AWESOME_HOME>/.env` | 用户级模型 key 和本机配置。 |
| `<AWESOME_HOME>/skills/` | 跨项目可用的个人 skills。 |
| `<AWESOME_HOME>/awesome-agent.yaml` | 用户级 extension 设置，包括 MCP sources。 |
| `<your-project>/skills/` | 当前仓库的项目级 skills。 |
| `<your-project>/awesome-agent.yaml` | 项目级 extension 设置。 |

Windows 上，`AWESOME_HOME` 默认是 `%LOCALAPPDATA%\awesome-agent`。其它平台
默认是 `~/.awesome-agent`。你可以用 `AWESOME_HOME` 环境变量覆盖默认路径。

模型密钥不会从项目 `.env` 读取。

## 常用命令

在 `awesome` 中使用这些命令：

| 命令 | 作用 |
| --- | --- |
| `/help` | 查看可用命令。 |
| `/config` | 查看当前生效的 Awesome 路径和 key 状态。 |
| `/status` | 查看当前会话状态。 |
| `/skills` | 列出可用 skills。 |
| `/mcp` | 查看已配置的 MCP servers。 |
| `/quit` | 退出 TUI。 |

`awesome-agent start` 只作为 Local API 开发时的备用/调试 supervisor 使用。

## 文档

- [文档地图](docs/README.md)
- [Quickstart](docs/getting-started/quickstart.md)
- [快速开始](docs/getting-started/quickstart.zh-CN.md)
- [用户指南](docs/user-guide/README.md)
- [运维指南](docs/operations/README.md)
- [架构](ARCHITECTURE.md)
- [安全模型](docs/architecture/security-model.md)

## 安全提示

只在你信任的项目中运行 Awesome。不要把 API key 提交到 Git；请把它们放在
操作系统环境变量或 `<AWESOME_HOME>/.env` 中。
