# 快速开始

Awesome 是本地终端 Coding Agent。唯一产品界面是 Ink `awesome`，其后运行本地
Python `awesome-core` 进程；不需要 API Server、PostgreSQL、Worker 或 Docker
Service。

## Phase 4 源码预览

面向最终用户的一键安装器将在 Phase 4 后续 PR 中交付。当前分支只提供贡献者
源码预览，不代表最终安装体验。

源码预览需要 Git、Python 3.12 与 uv，以及 Node.js 22 与 npm。这些只是开发依赖；
正式发行用户不需要手动安装 Python、Node、uv 或 npm。

```powershell
uv sync --extra memory --dev
npm --prefix tui ci
npm --prefix tui run build
$env:PATH = "$(Resolve-Path .venv\Scripts);$env:PATH"
node tui/dist/cli/index.js --help
```

```bash
uv sync --extra memory --dev
npm --prefix tui ci
npm --prefix tui run build
export PATH="$PWD/.venv/bin:$PATH"
node tui/dist/cli/index.js --help
```

## Provider 凭据

创建 `<AWESOME_HOME>/.env`，至少配置一个正式支持的 Provider：

```dotenv
DEEPSEEK_API_KEY=
MOONSHOT_API_KEY=
```

Windows 默认用户数据目录是 `%LOCALAPPDATA%\Awesome`；macOS/WSL2 默认是
`~/.awesome`。Mem0 Cloud 是可选能力，使用 `MEM0_API_KEY`。

## 在 Workspace 中启动

在希望 Awesome 使用的目录中启动已构建 Ink 入口。首次进入目录必须确认 trust；
拒绝 trust 会直接退出，不会运行模型或工具。

```powershell
cd E:\path\to\project
node E:\path\to\awesome-agent\tui\dist\cli\index.js
```

最终 V1 文档会使用一键安装命令和全局 `awesome` 启动器替换这条源码预览路径。
