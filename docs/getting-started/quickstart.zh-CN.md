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

## 从源码启动

如果你准备修改 Awesome 本身，可以直接运行当前仓库中的源码。开发模式与
正式安装版使用相同的 Python Core、私有 stdio 协议和 Ink TUI，但开发数据会
单独保存，避免影响已经安装的 Awesome。

### 准备开发环境

请先安装 [Git](https://git-scm.com/downloads)、
[uv](https://docs.astral.sh/uv/getting-started/installation/) 和
[Node.js 22 或更高版本](https://nodejs.org/)。npm 会随 Node.js 一起安装；
无需单独安装 Python，uv 会为项目准备所需的 Python 3.12 环境。

### 克隆仓库

```text
git clone https://github.com/JAGGER-L/awesome_agent.git
cd awesome_agent
```

### 安装依赖

```text
uv sync --locked --extra memory
npm ci --prefix tui
```

第一条命令会创建 `.venv`，以 editable 模式安装 Python Core，并安装可选的
Mem0 Cloud 集成；第二条命令会按锁文件安装 Ink TUI 的依赖。

### 一键启动当前源码

在 Awesome 仓库根目录运行：

```text
uv run awesome-dev
```

`awesome-dev` 会检查开发环境、构建当前 TUI 源码，并直接启动 Awesome。
Python Core 仍由 TUI 作为私有子进程启动，不需要再打开一个终端或手动运行
服务。

如果希望用当前 Awesome 源码处理另一个项目，可以运行：

```text
uv run awesome-dev --workspace <项目路径>
```

指定目录会成为 workspace，并正常显示 trust 确认。开发数据默认保存在
Awesome 仓库内已被 Git 忽略的 `.awesome-dev/home`，日志目录保留在
`.awesome-dev/logs`；目标项目中不会写入 Awesome 的配置或运行状态。

### 配置模型

源码模式与正式安装版使用同一套配置流程。启动后运行 `/auth`，选择
DeepSeek 或 Kimi，再选择可用的凭据来源；需要录入密钥时使用专用的遮罩
输入框。凭据保存在开发数据目录中，不会写入源码仓库或目标项目。

只有确实需要更换开发数据位置时，才在启动前设置 `AWESOME_HOME`。

### 运行检查

先执行与本次修改直接相关的检查：

```text
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run pytest <相关测试路径> -q
npm --prefix tui run format:check
npm --prefix tui run lint
npm --prefix tui run typecheck
npm --prefix tui test -- --run <相关测试路径>
```

准备发布或交付跨模块修改前，再按[测试指南](../development/testing.md)运行
完整验证。

### 修改代码后继续运行

结束当前 Awesome 会话，然后再次运行 `uv run awesome-dev`。启动器每次都会
重新构建 TUI，editable 安装的 Python Core 也会直接读取最新源码。开发模式
暂不提供热重载，避免在活跃 Thread 中重启 Core 后留下不确定状态。

只构建生产版 TUI、不启动 Awesome 时，运行：

```text
npm --prefix tui run build
```

### 常见问题

- 缺少 `.venv` 或 `awesome-core` 时，运行 `uv sync --locked --extra memory`。
- 缺少 `tui/node_modules` 时，运行 `npm ci --prefix tui`。
- 找不到 npm 或 Node.js 版本过低时，安装 Node.js 22 或更高版本。
- `--workspace` 只能指向已经存在的目录，启动器不会自动创建项目目录。
- 请在交互式终端中启动；Ink 界面不能通过非交互管道运行。

如果源码版本提示状态 schema 不兼容，请先退出 Awesome。确认启动错误界面
显示的状态路径就是当前仓库的开发数据路径，然后再执行：

```powershell
Resolve-Path .\.awesome-dev\home\state
Remove-Item -LiteralPath .\.awesome-dev\home\state -Recurse -Force
uv run awesome-dev
```

macOS 或 WSL2 使用：

```bash
realpath .awesome-dev/home/state
rm -rf -- .awesome-dev/home/state
uv run awesome-dev
```

只删除已经核对过的 `state` 目录。开发环境中的 `config.yaml`、`ui.json` 和
`<AWESOME_HOME>/.env` 位于该目录之外，不会被删除。这个操作只重置可丢弃的
开发会话和 checkpoint，不是数据迁移。

开发模式不会替换或修改系统中已经安装的 `awesome` 命令。正式安装版使用
用户数据目录和预构建文件；`uv run awesome-dev` 使用当前 checkout，并默认
使用仓库内的 `.awesome-dev` 开发数据目录。
