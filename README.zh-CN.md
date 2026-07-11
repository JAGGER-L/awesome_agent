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
- 使用 Skills、MCP 工具、本地 Memory 和 Mem0 Cloud 扩展能力；
- 使用 DeepSeek 和 Kimi 模型。

Awesome 最开始提供 `ls`、`read_file`、`write_file`、`edit_file`、`delete`、
`glob`、`grep` 和 `execute`。扩展可以继续增加工具，Awesome 不限制为八个工具。
本地文件 Memory 与 Mem0 Cloud 相互独立，二者默认关闭。

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

在 `<AWESOME_HOME>/.env` 中至少配置一个模型密钥：

```dotenv
DEEPSEEK_API_KEY=...
# 或
MOONSHOT_API_KEY=...
```

然后在项目目录中启动 Awesome：

```text
cd <project>
awesome
```

首次进入一个目录时，Awesome 会显示完整路径并询问是否信任。只有在你了解该项目、
并愿意让 Awesome 读取和操作其中内容时才选择 Yes。

常用启动参数：

```text
awesome --continue
awesome --resume
awesome --resume <thread_id>
awesome --version
awesome --help
```

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

## 安全

只信任你了解的项目，保留修改前先检查 `/diff`。密钥应存放在操作系统环境或
`<AWESOME_HOME>/.env` 中，不要写入项目文件。
