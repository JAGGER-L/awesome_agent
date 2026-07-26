# 开发环境设置

本指南将为 Python Core、Ink TUI、测试和文档工作建立可复现的本地 checkout。

## 要求

- Git；
- 用于锁定 Python 3.12 环境的 [uv](https://docs.astral.sh/uv/)；
- Node.js 22.23.1 或更高版本及 npm。TUI package 声明的最低版本是 22.23.1，CI 也会
  覆盖 Node 24；
- Windows、macOS 或 Linux 上受支持的 host terminal。

`uv` 会准备仓库所需的精确 Python 3.12 环境。不要把项目依赖安装到全局 Python 环境。

对于开发，Git 和 Node 是 host 前置条件。最终用户 installer 使用另一套私有 runtime
流程，不是贡献者环境设置方式。

## Clone 并创建分支

```powershell
git clone https://github.com/JAGGER-L/awesome_agent.git
Set-Location awesome_agent
git switch -c codex/my-change
git status --short --branch
```

使用专用 branch 或 worktree。编辑前阅读 `AGENTS.md`，在存在时检查
`.codex/exec-plans/active/` 中的 active plan，并确认是否有其他 task 拥有重叠文件。

## 安装锁定依赖

在仓库根目录运行：

```powershell
uv sync --locked --extra memory --dev
npm ci --prefix tui
```

这些命令的理由：

- `--locked` 拒绝解析出不同于 `uv.lock` 的依赖图；
- `--extra memory` 安装可选 Mem0 adapter，使完整本地测试套件可以导入并运行它；
- `--dev` 安装 Ruff、mypy、pytest、coverage 和审计工具；
- `npm ci` 精确安装 `tui/package-lock.json`，并拒绝 lock drift。

对于绝不会接触 Memory 的窄范围 Python-only 变更，`uv sync --locked --dev` 就足够。
运行完整套件或准备 release candidate 前，请使用完整环境。

## 启动当前 checkout

```powershell
uv run awesome-dev
```

开发 launcher 会：

1. 校验 checkout、Node/npm、`.venv` 和 TUI 依赖；
2. 运行 TUI production build；
3. 除非已有设置，否则将 `AWESOME_HOME` 指向被忽略的 `.awesome-dev/home`；
4. 将 checkout 中的 `awesome-core` entry point 加入 child `PATH`；
5. 在当前目录启动构建后的 TUI。

打开其他工作区：

```powershell
uv run awesome-dev --workspace C:\path\to\project
```

开发状态与预留日志目录默认为：

```text
.awesome-dev/
  home/    # isolated Awesome state/config/memory for this checkout
  logs/    # reserved by the launcher; not currently populated automatically
```

这样，普通开发不会使用已安装产品的 home。Core stderr 目前保存在 TUI 的有界内存 ring
中，用于生命周期诊断，不会持久化到 `logs/`。选中的项目仍是真实 host workspace，
因此仍应正常对待 trust 与 tool approval。

## 安全配置测试提供商

可以通过 `/auth` 进行交互式 provider 设置。本地自动测试使用确定性 fake，不需要凭据。

Live release 检查读取 process-scoped environment variable。请交互输入真实值，避免将其
写入命令行或 shell history：

```powershell
$env:AWESOME_RUN_EXTERNAL = "1"
$secret = Read-Host "DeepSeek API key" -AsSecureString
$env:DEEPSEEK_API_KEY = [Net.NetworkCredential]::new("", $secret).Password
$secret = Read-Host "Moonshot API key" -AsSecureString
$env:MOONSHOT_API_KEY = [Net.NetworkCredential]::new("", $secret).Password
$secret = Read-Host "Mem0 API key" -AsSecureString
$env:MEM0_API_KEY = [Net.NetworkCredential]::new("", $secret).Password
Remove-Variable secret
uv run --extra memory pytest -q tests/external/test_release_services.py
Remove-Item Env:AWESOME_RUN_EXTERNAL, Env:DEEPSEEK_API_KEY, `
  Env:MOONSHOT_API_KEY, Env:MEM0_API_KEY -ErrorAction SilentlyContinue
```

普通产品使用优先选择 `/auth`；CI 应使用平台的 secret injection，而不是 workflow 中的
literal value。绝不把真实值放入 shell history、tracked `.env`、fixture、snapshot、log、
plan 或 PR description。只记录脱敏结果和诊断码。

## 快速环境检查

```powershell
uv lock --check
uv run python --version
node --version
npm --version
uv run python -c "import awesome_agent; print(awesome_agent.__version__)"
node tui/scripts/sync-version.mjs --check
```

Python 版本必须满足 `>=3.12,<3.13`。Core package version、TUI package/lock/generated
source、installer 与 `VERSION` 会在其他位置检查；不要手动编辑生成的版本文件。

## 常见环境设置失败

### `awesome-dev` 找不到 Core

运行：

```powershell
uv sync --locked --extra memory --dev
```

Launcher 预期在 Windows 的 `.venv/Scripts` 或 POSIX 的 `.venv/bin` 下找到
`awesome-core`。

### 缺少 TUI 依赖

运行：

```powershell
npm ci --prefix tui
```

不要替换为 `npm install`；后者可能改变 lockfile。

### Node 被拒绝

使用 Node 22.23.1 或更新的兼容版本。`awesome-dev` 检查主版本 22 或更高，而 package
engine 与 CI 提供更严格的受支持 baseline。

### TUI build 在版本检查中失败

先检查 `VERSION`、`tui/package.json`、`tui/package-lock.json` 和
`tui/src/version.ts`。有意变更版本时运行：

```powershell
npm --prefix tui run version:sync
```

审查产生的每一处变更。普通 feature 工作应恢复意外版本编辑，而不是把它们同步出去。

### Application 状态不兼容

不要手动删除数据库。启动产品并对旧 schema 使用类型化 reset-or-exit 流程。更新或未知
schema 是停止条件；请使用正确 checkout 或升级路径。

### 另一个会话占用工作区

关闭选择了同一工作区的另一个 Awesome 会话。Path 与 physical-identity lease 会有意
阻止并发 Core authority。

### Checkout 干净，但生成输出过期

TUI build 会移除并重新生成 `tui/dist`。Release output 位于 `dist/`。这些都是生成
artifact；除非仓库契约明确变化，否则不要提交。

## IDE 与 shell 说明

- 通过 `uv run` 运行 Python 工具，确保 editor/terminal 结果使用锁定环境。
- 将 Python language server 指向 `.venv` 并启用 strict type checking。
- 保持 LF 换行；Ruff 与 Biome 会强制仓库格式。
- Windows 上使用 PowerShell 原生文件操作处理仓库，不要通过混合 shell 移动/删除递归目标。
- 平台特定文件系统和进程行为必须在该平台测试；Linux 模拟不能证明 Windows junction
  或 Job Object 契约。

接下来，请在[测试](testing.zh-CN.md)中选择最小 gate。
