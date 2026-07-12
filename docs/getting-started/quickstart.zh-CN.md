# 快速开始

按照下面五个步骤安装 Awesome，并完成第一次成功会话。

## 1. 安装 Awesome

### macOS 或 WSL2 Ubuntu

```bash
curl -fsSL https://github.com/JAGGER-L/awesome_agent/releases/latest/download/install.sh | sh
```

### Windows

```powershell
irm https://github.com/JAGGER-L/awesome_agent/releases/latest/download/install.ps1 | iex
```

打开新终端并验证安装结果：

```text
awesome --version
```

Git 是可选能力，Awesome 不会自动安装；工作需要 Git 时，请使用
[Git 官方安装程序](https://git-scm.com/downloads)。

## 2. 在项目中启动

```text
cd <project>
awesome
```

启动 Awesome 时所在的目录就是 workspace。

## 3. 信任 Workspace

Awesome 会在使用项目指令或工具前显示 workspace 路径。只有在你认识并
信任该项目时才选择 Yes；选择 No 会直接退出。
信任后默认进入 Request approval 模式，编辑、删除和 shell 命令执行前会询问。
可运行 `/permissions` 查看当前模式。

## 4. 配置模型

尚未配置模型 Provider 时，按 Enter 或运行 `/model`。选择 DeepSeek 或 Kimi，
在遮罩输入框中粘贴 API Key，然后选择模型。Awesome 会在保存前验证密钥。

之后可使用 `/auth` 添加、替换或删除 Provider 凭据。不要把 API Key 写入
slash command 参数或聊天消息。

## 5. 快速验证

发送一个只读请求：

```text
分析这个项目的结构，并告诉我应该从哪里开始阅读。
```

## 继续了解

- [命令](../user-guide/commands.md)
- [配置](../user-guide/configuration.md)
- [故障排查](../user-guide/troubleshooting.md)
