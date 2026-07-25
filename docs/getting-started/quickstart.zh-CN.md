# 快速开始

按照下面五步完成一次成功的只读 Awesome 会话。主机要求、安装器行为、升级和修复说明
请参阅[安装指南](installation.md)。

## 1. 安装 Awesome

Apple Silicon macOS 或 WSL2 Ubuntu 24.04 x64：

```bash
curl -fsSL https://github.com/JAGGER-L/awesome_agent/releases/latest/download/install.sh | sh
```

Windows 11 x64：

```powershell
irm https://github.com/JAGGER-L/awesome_agent/releases/latest/download/install.ps1 | iex
```

打开一个新终端并验证版本：

```text
awesome --version
```

Awesome 已包含 Python 和 Node.js 运行时。Git 是可选能力，可从
[Git 官方网站](https://git-scm.com/downloads)单独安装。

## 2. 在项目中启动

选择一个你了解的项目，从项目根目录启动：

```text
cd <项目目录>
awesome
```

启动目录会成为 Workspace。以后可用 `awesome --continue` 恢复该 Workspace
最近的 Thread，或用 `awesome --resume` 进行选择。

## 3. 信任 Workspace

确认界面显示的路径无误后再选择 **Yes**。选择 **No** 会直接退出，并且不会加载该
Workspace 的项目配置、项目指令或工具。

信任后默认进入 **Request approval** 模式：读取自动允许，写入、删除和 shell 命令
执行前询问。`/permissions` 可查看当前模式。信任并不等于操作系统沙箱；对不可信项目
应先使用外部隔离环境。

如果根目录中普通的 `AGENTS.md` 通过检查，Awesome 会在本会话中只读取一次，作为
强制项目指令快照。被拒绝的指令文件会整份忽略，并在 Welcome、状态栏和 `/doctor`
中显示原因。

## 4. 配置模型

尚未配置模型 Provider 时，在设置提示上按 Enter，或运行：

```text
/model
```

选择 DeepSeek 或 Kimi，在遮罩输入框中粘贴 API Key，再选择模型。Awesome 会在
保存前验证密钥。之后可用 `/auth` 添加、替换、删除凭据或选择凭据来源。不要把
API Key 粘贴到聊天消息或 slash command 参数中。

创建正式密钥前，请阅读 [What You Need](README.md#what-you-need)，确认官方密钥
入口、Kimi 中国区/全球区选择、账户与网络前提，以及模型上下文会发送给所选第三方
Provider 的数据边界。

## 5. 快速验证

发送一个只读请求：

```text
分析这个项目的结构，并告诉我应该从哪里开始阅读。
```

成功收到回答，说明 Workspace、Thread、模型、上下文、流式输出和只读工具链路都能
正常工作，且项目文件没有被修改。可运行 `/context`、`/tools` 和 `/status` 查看
Awesome 实际使用的状态。

## 接下来读什么

- 通过 [Workspace、Thread、Turn 与 Operation](../concepts/workspace-thread-turn.md)
  理解生命周期。
- 在[权限与安全](../user-guide/permissions.md)中选择合适的审批方式。
- 按[用户指南](../user-guide/README.md)建立日常工作流。
- 某一步失败时查看[故障排查](../user-guide/troubleshooting.md)。
