# 快速开始

[English](quickstart.md) | [简体中文](quickstart.zh-CN.md)

本文说明如何通过 Local CLI、Local API 和 Docker API/Web 三条路径配置、
启动、验证并运行 `awesome_agent`。

当前 “Web” surface 是本地 FastAPI inspection surface 和生成的 API docs，
还不是托管式多用户 Web 应用。

Makefile 命令是主要启动契约。Docker API 使用 `make docker-init` 和
`make docker-start`；本地 API 开发使用 `make check`、`make install`、
`make setup-sandbox` 和 `make dev`；本地交互式 CLI 使用 `awesome`。
现有 PowerShell 脚本保留为 Windows fallback 入口。

持久化 profile 和存储契约见
[runtime profiles and startup](../design-docs/runtime-profiles-and-startup.md)。

## 前置依赖

- Python 3.12
- `uv`
- GNU Make，用于主要 Makefile 命令
- Docker Desktop 或兼容 Docker engine
- Git
- helper scripts 使用 Windows PowerShell

## 源码 Checkout

从仓库源码开始：

```powershell
git clone https://github.com/JAGGER-L/awesome_agent.git
cd awesome_agent
make check
make install
```

`make check` 检查宿主机前置依赖。`make install` 同步 Python 环境，并安装本地
`awesome` 和 `awesome-agent` 命令。下面所有启动路径都假设已经完成这一步。

## 配置

| 文件 | 作用 |
| --- | --- |
| `<AWESOME_HOME>/.env` | 用户级 Awesome secrets 和 runtime settings，由 `Settings` 加载；不要提交真实值。 |
| `awesome-agent.yaml` | 项目级 extension sources，例如 skills。不要在这里保存 secrets。 |
| `skills/` | 项目级 skill packages，每个 package 包含 `SKILL.md`。 |
| `<AWESOME_HOME>/awesome-agent.yaml` | 用户级 extension sources，包括 MCP sources。 |
| `<AWESOME_HOME>/skills/` | 用户级 skill packages，每个 package 包含 `SKILL.md`。 |
| `<AWESOME_HOME>/config.toml` | `awesome-agent config root add/list/remove` 管理的本地 allowed-root 状态。 |
| `<AWESOME_HOME>/threads/<thread_id>/workspace/` | Thread/Conversation 的持久 model-visible workspace。AIO Docker 中映射为 `/mnt/user-data/workspace/`。 |
| `<AWESOME_HOME>/runs/<run_id>/artifacts/` | 默认本地 artifact 存储。`AWESOME_AGENT_ARTIFACT_ROOT` 覆盖 runs root，不覆盖 per-run 后缀。 |

`AWESOME_HOME` 在 Windows 默认是 `%LOCALAPPDATA%\awesome-agent`，其它平台默认是
`~/.awesome-agent`。可以设置 `AWESOME_HOME` 覆盖默认值。

创建用户级 Awesome home 和 env 文件：

```powershell
awesome init
```

Awesome Agent 当前产品 conversation turns 只支持官方 DeepSeek provider。
请在本地 Awesome env 文件或 shell 环境中配置
`AWESOME_AGENT_DEEPSEEK_API_KEY`。项目 `.env` 不作为 Awesome provider
credentials 来源。产品 runtime 不支持自定义 DeepSeek-compatible base URL。
默认角色模型是 Leader 使用 `deepseek-v4-pro`，Teammate、Verifier 和
Subagent 使用 `deepseek-v4-flash`。

## 启动路径矩阵

| 模式 | 适用场景 | 命令 | 成功信号 |
| --- | --- | --- | --- |
| Local CLI | 交互式本地 coding-agent 入口 | `awesome`, `awesome commands` | 不启动 API 也能打印 slash commands。 |
| Local API | 从宿主 Python 检查 API + Worker | `make check`, `make install`, `make setup-sandbox`, `make dev` | `/health` 和 `/ready?profile=api` 返回 healthy JSON。 |
| Docker API/Web | 通过容器化 API 使用浏览器/API inspection | `make docker-init`, `make docker-start` | `http://127.0.0.1:8000/docs` 打开 FastAPI docs。 |
| Local CLI fallback | 首次本地运行和开发 | `.\scripts\quickstart.ps1` | Probe Run 完成，并可打印 diagnostics。 |

## Local API

运行 Makefile-first 本地 API 路径：

```powershell
make check
make install
make setup-sandbox
make dev
```

`make setup-sandbox` 构建 AIO Docker sandbox service image
`awesome-agent-sandbox:aio`。`make dev` 启动 PostgreSQL、执行 migrations、
启动 API + Worker，并打印本地 API 和 docs URL。它不会启动 CLI/TUI。

## Local CLI

首次启动本地 CLI：

```powershell
awesome init
awesome doctor
cd E:\my-project
awesome
```

`awesome init` 会创建 `<AWESOME_HOME>/config.yaml`、`<AWESOME_HOME>/.env`、
`<AWESOME_HOME>/awesome-agent.yaml` 以及 `skills`、`state`、`runs`、`logs`
运行目录，并且不会覆盖已有 secrets。模型调用前，请把
`AWESOME_AGENT_DEEPSEEK_API_KEY` 设置在 shell、操作系统环境变量、密码管理器或
`<AWESOME_HOME>/.env` 中。

`awesome doctor` 只检查本地 CLI 首次使用路径：user config、Settings 中有效的
`AWESOME_AGENT_DEEPSEEK_API_KEY`、官方 DeepSeek base URL、当前项目 config
是否存在，以及 Awesome user env 是否存在。它不检查 API server、Docker、
PostgreSQL、Worker 或 sandbox 健康状态。开发者/运维诊断使用
`awesome-agent doctor --profile api` 或
`awesome-agent doctor --profile runtime`。

打开本地交互入口：

```powershell
cd E:\my-project
awesome
awesome commands
```

从希望 agent 工作的项目目录运行 `awesome`。启动目录会成为默认 thread
context。如果目录是 Git checkout，Runs 会继承该 repository；如果不是 Git
checkout，CLI 会使用 workspace-only mode，并且仍接受用户消息 turn。

`awesome` 启动前不需要 API。它默认使用 local CLI profile 和 LocalSandbox，
然后打开 chat-first 本地 CLI/TUI。这是 trusted-local convenience mode；
API profiles 默认使用 AIO Docker。直接操作、diagnostics 和脚本化检查使用
`awesome-agent` 子命令。

## Local CLI Fallback

运行自动化本地路径：

```powershell
.\scripts\quickstart.ps1
```

只预览步骤、不产生副作用：

```powershell
.\scripts\quickstart.ps1 -PlanOnly
```

脚本退出后保留 runtime：

```powershell
.\scripts\quickstart.ps1 -KeepRuntime
```

使用已经运行的 API + Worker：

```powershell
.\scripts\quickstart.ps1 -UseExistingRuntime
```

脚本会安装本地依赖、确保 Awesome user env 存在、启动 PostgreSQL、执行
migrations、启动 API + Worker、创建 ignored sample repository、验证
diagnostic probe，并打印第一次 read-only run inspection 步骤。除非传入
`-RunReadOnly`，否则不需要模型 key。

## Manual Local API Fallback

手动启动本地依赖和 supervised runtime：

```powershell
.\scripts\bootstrap.ps1
awesome init
docker compose up -d postgres
.\scripts\migrate.ps1
.\.venv\Scripts\awesome-agent.exe doctor --profile api
.\.venv\Scripts\awesome-agent.exe start
```

`awesome-agent start` 是 API + Worker 同进程组的 fallback/debug supervisor。
正常本地 API 开发优先使用 `make dev`。

API 地址是 `http://127.0.0.1:8000`。

检查 readiness：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod "http://127.0.0.1:8000/ready?profile=api"
```

## Docker API/Web

准备并启动 Docker API stack：

```powershell
make docker-init
make docker-start
```

Docker mode 不启动 CLI。CLI/TUI 仍在本地使用 `awesome`。Docker Compose 会启动
PostgreSQL、AIO sandbox service、API 和 Worker。启动后打开
`http://127.0.0.1:8000/docs` 查看 FastAPI docs。

## Docker API Compatibility Script

运行容器化 API + Worker 路径：

```powershell
.\scripts\docker-quickstart.ps1
```

预览 Docker 步骤：

```powershell
.\scripts\docker-quickstart.ps1 -PlanOnly
```

脚本会确保 Awesome user env 存在，运行
`docker compose up -d --build postgres sandbox api worker`，等待 API
readiness，并打印带 `--api-url`、指向容器化 API 的 CLI next steps。这是
开发者/运维兼容路径，不是本地 CLI 产品的主要首次启动路径。

## Manual Docker API Fallback

直接启动 Docker services：

```powershell
docker compose up -d --build postgres sandbox api worker
```

检查 API：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod "http://127.0.0.1:8000/ready?profile=api"
```

打开 `http://127.0.0.1:8000/docs` 查看生成的 FastAPI 文档。

Docker runtime data 位于 `awesome_agent_runtime` volume。Per-run artifacts
在容器内保存到 `/var/lib/awesome-agent/runs/<run_id>/artifacts/`。
Model-visible workspace files 位于 `awesome_agent_user_data` volume，并挂载为
`/mnt/user-data/workspace/`。

## 无模型 Key 验证

授权父目录并注册一个干净的 Git checkout：

```powershell
.\.venv\Scripts\awesome-agent.exe config root add <parent-directory>
.\.venv\Scripts\awesome-agent.exe repo add <repository-path>
```

不需要模型 key 即可验证 durable runtime：

```powershell
.\.venv\Scripts\awesome-agent.exe probe --repo <repository-path>
.\.venv\Scripts\awesome-agent.exe diagnostics <run-id>
```

Docker API mode 下，为 CLI 命令添加 `--api-url http://127.0.0.1:8000`。

`/health` 是进程 liveness。`/ready?profile=api` 检查 API dependencies。
`/ready?profile=runtime` 还会检查 provider configuration 和 Worker heartbeat
等 runtime dependencies。

## 第一条模型驱动用户消息

在操作系统环境变量或 `<AWESOME_HOME>/.env` 中设置
`AWESOME_AGENT_DEEPSEEK_API_KEY`，重启本地交互 runtime，从项目目录打开
`awesome`，然后发送一条普通用户消息：

```text
Build a single-file HTML timer in this folder.
```

该消息会创建内部 conversation Run 和 Leader Agent，并通过 embedded local
runtime 路径执行。

## 关闭和清理

用 `Ctrl+C` 停止本地 supervised runtime。

停止 Docker services：

```powershell
docker compose down
```

检查或清理 managed workspaces：

```powershell
.\.venv\Scripts\awesome-agent.exe workspace list
.\.venv\Scripts\awesome-agent.exe workspace cleanup --run-id <run-id>
```

## 故障排查

- 如果 `/health` 失败，说明 API process 不可达。
- 如果 `/ready?profile=api` 失败，检查 PostgreSQL、migrations 或 settings。
- 如果需要 Docker API logs，运行 `docker compose logs api`。
- 如果需要 Docker Worker logs，运行 `docker compose logs worker`。
- 如果 Run 卡住，运行 `awesome-agent diagnostics <run-id>`。

## 本地资源建议

使用外部 API 模型时，单个本地开发会话可从 4 vCPU、8 GB memory 和 20 GB
可用磁盘开始。多个 concurrent Runs、team mode、Docker image builds 或大型
repository workspaces 需要更多 memory 和 disk。
