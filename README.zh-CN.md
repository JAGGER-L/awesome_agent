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
自动化程度较高的工程工作流。唯一产品入口是本地 Ink `awesome` 界面，其后运行
私有的 Python Core 进程。


## 产品界面

| 方式 | 适合场景 | 启动命令 |
| --- | --- | --- |
| Local CLI | 在本地项目目录中通过终端工作；不需要 API Server、PostgreSQL、Worker 或 Docker Service。 | `cd <your-project>` 然后运行 `awesome` |

公开的 `awesome` 命令始终启动本地 Ink
界面及其私有 Python Core 进程。

从项目目录运行 `awesome`。启动目录会成为默认 thread context。如果它是一个
Git checkout，runs 会继承该 repository；否则 Awesome 会使用 workspace-only
mode，并且仍然接受用户消息 turn。普通用户消息是唯一的产品执行创建路径。

## 快速开始

V1 一键安装器将在下一个 Phase 4 PR 中交付。当前请使用贡献者源码预览：

```powershell
uv sync --extra memory --dev
npm --prefix tui ci
npm --prefix tui run build
$env:PATH = "$(Resolve-Path .venv\Scripts);$env:PATH"
node tui/dist/cli/index.js --help
```

POSIX 等价命令和 workspace 启动方式见
[快速开始](docs/getting-started/quickstart.zh-CN.md)。

## 配置基础

Awesome 会把自己的用户文件放在项目目录之外。

| 路径 | 作用 |
| --- | --- |
| `<AWESOME_HOME>/.env` | 用户级模型 key 和本机配置。 |
| `<AWESOME_HOME>/config.yaml` | 用户级 Provider、budget、memory、skill 和 MCP 配置。 |
| `<AWESOME_HOME>/skills/` | 跨项目可用的个人 skills。 |
| `<your-project>/skills/` | 当前仓库的项目级 skills。 |
| `<your-project>/.awesome/config.yaml` | trusted workspace 的 budget、skill 和 MCP 配置。 |

Windows 上，`AWESOME_HOME` 默认是 `%LOCALAPPDATA%\Awesome`。其它平台
默认是 `~/.awesome`。你可以用 `AWESOME_HOME` 环境变量覆盖默认路径。

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

## 文档

- [文档地图](docs/README.md)
- [Quickstart](docs/getting-started/quickstart.md)
- [快速开始](docs/getting-started/quickstart.zh-CN.md)
- [用户指南](docs/user-guide/README.md)
- [架构](ARCHITECTURE.md)
- [安全模型](docs/architecture/security-model.md)

## 安全提示

只在你信任的项目中运行 Awesome。不要把 API key 提交到 Git；请把它们放在
操作系统环境变量或 `<AWESOME_HOME>/.env` 中。
