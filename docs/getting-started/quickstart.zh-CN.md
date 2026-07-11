# 快速开始

本指南从一台全新受支持主机开始，完成一次可信的本地 coding session。

## 1. 安装并打开新终端

Apple Silicon macOS 或 WSL2 Ubuntu 24.04 x64：

```bash
curl -fsSL https://github.com/JAGGER-L/awesome_agent/releases/latest/download/install.sh | sh
```

Windows 11 x64 PowerShell：

```powershell
irm https://github.com/JAGGER-L/awesome_agent/releases/latest/download/install.ps1 | iex
```

打开新终端，让 PATH 包含 `awesome`，然后验证：

```text
awesome --version
awesome --help
```

Git 是可选能力，Awesome 不会安装它；需要时请使用
[Git 官方安装程序](https://git-scm.com/downloads)。

## 2. 配置 Provider

Windows 的 `AWESOME_HOME` 默认是 `%LOCALAPPDATA%\Awesome`，macOS/WSL2 默认
是 `~/.awesome`。在 `<AWESOME_HOME>/.env` 写入一个或两个密钥：

```dotenv
DEEPSEEK_API_KEY=...
MOONSHOT_API_KEY=...
```

如果两个都存在，可在 `<AWESOME_HOME>/config.yaml` 或进入产品后通过 `/model`
选择模型。当前仅支持 DeepSeek 和 Kimi。

## 3. 信任 workspace

```text
cd <workspace>
awesome
```

核对界面显示的路径。只有在信任其中的文件和项目指令时才选择 Yes；选择 No 会
直接退出，并且不会持久化 trust。

可以先提出无修改请求：`列出顶层文件并解释项目，不要修改任何内容。`

## 4. 修改并检查

让 Agent 做一个小改动，然后使用：

```text
/diff
/undo
/redo
```

undo/redo 覆盖 Change Journal 记录的文件工具修改；`execute` 产生的 shell 副作用
不保证可逆。

## 5. 稍后继续

用 `/status` 复制可恢复的 Thread ID，然后 `/quit` 退出。

```text
awesome --continue
awesome --resume
awesome --resume <thread_id>
```

`--continue` 恢复当前 workspace 最近的 thread；TUI 内的 `/resume` 提供相同的
thread 恢复流程。

## 默认值与诊断

- `/thinking` 显示当前模式并提供 on/off 选择；也可用 `/thinking on`、
  `/thinking off`。思考模式默认关闭。
- 本地文件 memory 与 Mem0 Cloud 相互独立，二者默认关闭；用 `/memory` 查看或配置。
- `/status` 显示产品、workspace、thread、模型、模式、memory、MCP、运行和配置状态。
- `/context` 与 `/usage` 分别显示上下文和最近一次 usage 详情。
- `/doctor` 检查本地配置、SQLite、checkpoint 与 Provider 可用性。

遇到问题先运行 `/doctor`。升级时先关闭所有 Awesome 进程，再重新运行原安装
命令；没有单独的 update 命令。
