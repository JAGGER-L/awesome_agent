# 快速开始

按照下面的步骤，在几分钟内开始使用 Awesome。

## 1. 安装 Awesome

### macOS 或 WSL2 Ubuntu

```bash
curl -fsSL https://github.com/JAGGER-L/awesome_agent/releases/latest/download/install.sh | sh
```

### Windows

```powershell
irm https://github.com/JAGGER-L/awesome_agent/releases/latest/download/install.ps1 | iex
```

打开新终端，然后检查安装结果：

```text
awesome --version
awesome --help
```

Git 是可选能力，Awesome 不会自动安装；工作流需要 Git 时，请使用
[Git 官方安装程序](https://git-scm.com/downloads)。

## 2. 配置模型

Windows 的 `AWESOME_HOME` 默认是 `%LOCALAPPDATA%\Awesome`，macOS 或 WSL2
默认是 `~/.awesome`。在 `<AWESOME_HOME>/.env` 中写入一个或两个密钥：

```dotenv
DEEPSEEK_API_KEY=...
MOONSHOT_API_KEY=...
```

Awesome 支持 DeepSeek 和 Kimi。如果两个密钥都存在，可在
`<AWESOME_HOME>/config.yaml` 中配置模型，或进入 Awesome 后使用 `/model`。

## 3. 在项目中启动

```text
cd <project>
awesome
```

启动 Awesome 时所在的目录就是 workspace。

## 4. 信任 Workspace

加载项目指令、配置、Skills、MCP 服务和工具前，Awesome 会显示 workspace
路径。只有在你认识并信任该项目时才选择 Yes；选择 No 会直接退出，也不会保存
信任记录。

## 5. 了解项目

先提出一个只读请求：

```text
分析这个项目的结构，并告诉我应该从哪里开始阅读。
```

## 6. 修改并检查

描述一个小改动。Awesome 完成后，使用下面的命令检查：

```text
/diff
```

## 7. 撤销或重做

对于文件工具产生的修改，可以使用：

```text
/undo
/redo
```

`execute` 运行的命令可能影响 Change Journal 之外的文件或外部工具，因此不一定
能够撤销。

## 8. 查看 Thread ID

```text
/status
```

`/status` 会显示当前 Thread ID，以及 workspace、模型、思考模式、Memory、MCP
和当前操作状态。

## 9. 稍后继续

使用 `/quit` 退出，之后可以继续最近的 Thread 或恢复指定 Thread：

```text
awesome --continue
awesome --resume
awesome --resume <thread_id>
```

## 10. 下一步

- [`/thinking`](../user-guide/commands.md) 可以查看或切换思考模式；默认关闭。
- 本地文件 Memory 与 Mem0 Cloud 相互独立，二者默认关闭；使用 `/memory` 查看。
- `/context` 和 `/usage` 分别显示上下文与模型用量。
- `/doctor` 检查配置、本地状态和模型可用性。

接下来可以阅读[命令](../user-guide/commands.md)、
[配置](../user-guide/configuration.md)或[故障排查](../user-guide/troubleshooting.md)。
升级时先关闭 Awesome，然后重新运行相同的安装命令。
