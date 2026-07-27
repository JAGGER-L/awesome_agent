# 测试与 CI

测试是在能够证明某项不变量的最低成本层提供证据。如果等价输入、取消、竞态、恢复、
协议对等端或平台仍能违反同一不变量，那么一次通过的复现并不够。

## 测试层级

| 层级 | 证明内容 | 位置 |
| --- | --- | --- |
| unit | 一项纯 policy、状态转换、adapter 或有界 failure | `tests/unit/`、`tui/tests/**` |
| integration | 跨本地组件/持久化边界的真实协作 | `tests/integration/` |
| E2E | 一条完整用户可见进程流 | `tests/e2e/`、`tui/tests/e2e/` |
| structural | package 所有权、inventory、依赖与源代码契约 | `tests/structural/`、`tui/tests/structural/` |
| packaging | 已安装 wheel/TUI/installer/release 形态 | `tests/packaging/`、`tui/tests/packaging/` |
| external | 显式启用的 live provider/network 证据 | `tests/external/` |

测试保护当前产品契约，而不是已经放弃的实现细节。如果架构有意移除某项行为，应更新契约
及其测试，而不是只为了保留过时形态而添加 adapter。

## 渐进式本地 gate

按成本递增顺序运行检查。较低 gate 失败时应停止，除非已经证明失败与当前变更无关。

### 1. Python 格式与 lint

```powershell
uv run ruff format --check src tests scripts
uv run ruff check src tests scripts
```

格式化有意的 Python 变更：

```powershell
uv run ruff format src tests scripts
```

### 2. 严格类型检查

```powershell
uv run mypy src tests scripts
```

Mypy 使用严格模式。不要为了逃避边界建模而添加 ignore。当前唯一 missing-import override
是 `pyproject.toml` 中声明的可选 Mem0 package 契约。

### 3. 聚焦测试

示例：

```powershell
uv run pytest -q tests/unit/core/tools/test_permissions.py
uv run pytest -q tests/unit/application/test_operation_controller.py
uv run pytest -q tests/integration/test_agent_turn.py
uv run pytest -q -k "shutdown or cancellation"
```

使用能覆盖变更所有者的最窄文件或 selection。测试需要可选 Memory 实现时，运行：

```powershell
uv run --extra memory pytest -q tests/integration/test_mem0_cloud.py
```

### 4. 结构与打包契约

```powershell
uv run pytest -q tests/structural tests/packaging
uv build --wheel --no-build-isolation
```

结构测试是可执行架构。失败时应审查职责归属，而不是对预期 inventory 进行搜索替换。

### 5. TUI gate

```powershell
npm --prefix tui run version:check
npm --prefix tui run format:check
npm --prefix tui run lint
npm --prefix tui run typecheck
npm --prefix tui test
npm --prefix tui run build
npm pack ./tui --dry-run
```

只要 Python payload、protocol method、event、command result 或 thread-transition 契约发生
变化，就要运行 TUI 测试，即使最初并未编辑 `.ts` 文件。Packaging test 比简单 dry-run
更强：它会执行全新 build、pack、安装 tarball 并运行已安装 CLI。保留显式的 build 后
`npm pack --dry-run`，作为便于人工阅读的内容检查；在 build 前运行可能检查过期 `dist`，
或无法证明 bin target。

### 6. Protocol v4 fixtures

```powershell
uv run python scripts/generate_protocol_fixtures.py --check
```

如果有意改变契约，请不带 `--check` 重新生成，检查所有 fixture 与 manifest 变更，更新
严格 TypeScript schema/presenter，然后重新运行 Python 与 TUI fixture 测试。不要手工编辑
生成的 JSON。

Web 变更使用 fake Tavily transport/provider suite，覆盖严格请求边界、`trust_env=False`、
显式 proxy 选择、每种稳定 HTTP failure、不自动 retry、permission choice 与 grant 撤销、
八次请求硬 budget、non-replayable recovery、citation finalization，以及 Python/TypeScript
round trip。Live Tavily 请求只作为显式 release gate；普通测试不得要求网络访问或真实 key。

### 7. 文档站

```powershell
npm ci --prefix site
npm --prefix site run check:navigation
npm --prefix site run check
$env:SITE_URL = "https://jagger-l.github.io"
$env:BASE_PATH = "/awesome_agent"
npm --prefix site run build
npm --prefix site run check:links
Remove-Item Env:SITE_URL, Env:BASE_PATH -ErrorAction SilentlyContinue
```

`check` 会同步源 Markdown、校验导航 manifest 并运行 Astro 检查。带 production base 的
build 也会生成 base-aware `dist/llms.txt`；built-link 检查能捕获 root-hosted dev server
可能隐藏的部署路径错误。

### 8. 完整确定性套件

```powershell
uv run --extra memory pytest -q tests/unit
uv run --extra memory pytest -q tests/integration
uv run --extra memory pytest -q tests/e2e
uv run --extra memory pytest -q tests/packaging tests/structural
```

Required CI 将 unit、integration 和 E2E 组合起来，要求 branch coverage 且最低覆盖率为
80%。不要通过低价值断言追逐数字；应覆盖决策、失败和状态转换。

## 风险到测试矩阵

| 变更 | 最低聚焦证据 | 跨越边界时补充 |
| --- | --- | --- |
| 纯 parser 或 policy | unit normal/boundary/negative case | dialect/platform 参数化 |
| Application 状态变化 | unit service test | Conversation/Storage 集成和 foreground race |
| Agent route/state | node/budget/state 单元测试 | 编译图集成与恢复 |
| 文件 mutation | tool + filesystem 单元测试 | Change Journal 集成、conflict、Windows reparse |
| shell process | command-policy + runner 测试 | 各平台真实 timeout/cancel/process-tree 测试 |
| provider adapter | 规范化 stream/error 单元测试 | Gateway 集成；live 检查只作 release 证据 |
| MCP/Skill/Memory | package 单元测试 | 原子 catalog/load 集成及 malformed/limit case |
| protocol/event/result | Python fixture 测试 | TypeScript schema、reducer、presenter、E2E stdio |
| TUI keyboard/mode | reducer/router 单元测试 | 组件流与 terminal E2E |
| storage schema/recovery | database 单元测试 | crash-window、lock、reset 与 packaging verifier |
| documentation/navigation | Markdown inventory/link 测试 | Astro check、production build、built-link 校验 |

## 设计稳健测试

### 证明不变量，而不是一种拼写

对于 parser 或安全边界，应测试原始输入、大小写/路径/后缀变体、嵌套 wrapper、复合/换行
形式，以及包含相似文字的无害字符串。只匹配一个复现的安全修复并不完整。

### 让并发具有确定性

使用 `asyncio.Event`、barrier、注入 fake 和受控 task handoff。覆盖两个顺序：

```text
Operation wins -> command/mutation is busy
command/mutation wins -> Operation is busy
```

还要证明清理会释放所有权，shutdown 会阻止新准入。不要依赖任意 sleep 来“可能”制造竞态。

### 单独处理取消

取消不是普通 failure。应断言：

- 清理有边界；
- 在需要处传播原始 `CancelledError`；
- 只存在一个终态 event 和一个 audit activity；
- 持久化状态为终态或显式可恢复；
- child process/reader 已回收；
- 不会自动重试并复制结果不确定的作用。

### 同时测试边界两侧

对于 byte、token、page、depth、node、timeout 和 queue 上限，应测试刚好低于、等于和高于
边界的值。还应包含在昂贵工作或外部 I/O 前失败的畸形形态。

### 保持确定性套件离线

Provider、Mem0、MCP 和进程测试使用 fake 或本地 fixture。普通 CI 不需要凭据。Live 行为
属于独立 release 证据，网络故障不能重新定义代码回归。

## 平台证据

文件系统和进程语义有实质差异：

- Windows：junction/reparse point、path alias、Job Object、`taskkill` 和锁定数据库 rename；
- POSIX：symlink、descriptor-relative path、process group 和 detached inode 行为；
- shell policy：CMD、POSIX 和 PowerShell 应在任意 host 上使用显式 dialect 参数。

平台 skip 记录的是缺失证据，不是通过的替代品。真实 Windows-only reparse/process 测试
应放在 Windows contracts job，并使用 nightly 三 OS 矩阵提供更广泛 system 证据。

Candidate installer hook 使 tag 前 release 证据无需先发布 asset 就能执行：在
`127.0.0.1` 提供 manual-dispatch artifact，再用显式 candidate 变量运行 installer。证据
仍必须来自 Windows 11 x64、WSL2 Ubuntu 24.04 x64 与 Apple Silicon macOS。Windows
Server hosted job 不是 Windows 11，普通 Ubuntu runner 也不是 WSL2。

## Required CI

`.github/workflows/ci.yml` 在 pull request、推送到 `main`、merge-queue revision 和手动
dispatch 时运行。其稳定 `Required` aggregator 依赖：

| Job | 证据 |
| --- | --- |
| Python quality | actionlint、lock check、Ruff、strict mypy、Protocol fixture |
| Python tests and coverage | unit + integration + E2E 与 branch coverage |
| Windows contracts | Windows 敏感的 Core/Application/Protocol/extension 测试加 installer 源代码/解析契约；不是真实下载安装流程 |
| Structural and packaging contracts | 所有权、inventory、wheel build 与干净安装 |
| TUI matrix | Ubuntu 上 Node 22.23.1/24 与 Windows 上 22.23.1 |
| Docs site | 导航、Astro check、base-aware build、built-link 检查 |

Branch rule 应要求稳定的 `Required` check，而不是 matrix 生成的 job 显示名称。

Pull-request revision 会取消过期 Required CI run。Job 使用显式 deadline，third-party Action
固定到完整 commit hash。Required CI 会下载 checksum-pinned actionlint binary，再校验
workflow。

## Security 与 nightly CI

`.github/workflows/security.yml` 提供稳定 `Security required` aggregator：

- 对 pull-request 新依赖执行 dependency review；
- 对 Python 和 JavaScript/TypeScript 执行 CodeQL；
- 通过 pip-audit 的 hash-validating PyPI 路径审计 locked Python export；
- 对已校验的 name/version graph 补充 OSV lookup；
- 对 TUI 和文档站执行 npm lock audit。

OSV 命令使用 `--disable-pip`，只作为补充 advisory lookup。它不会独立校验 artifact；
PyPI 路径会先执行 hash 检查。

`.github/workflows/nightly.yml` 在 Ubuntu、Windows 和 macOS 上运行完整 Python suite 与
TUI/package 测试，以及 npm audit。Nightly 证据扩大平台覆盖，但不能代替聚焦 PR 回归。

## 已知 CI 证据缺口

Candidate-artifact installer smoke 现在是显式 manual tag 前 gate，但不会被错误标记为
hosted CI 证据。Workflow 仍适合增加以下四项自动化；应把它们作为聚焦 job 添加，不应
夸大现有 gate：

1. **从源代码派生的文档契约。** 从 `COMMAND_OWNERS`、配置模型、Tool 注册和 Protocol
   method model 生成或比较 reference inventory，避免新公共契约因忘记更新手抄测试列表
   而绕过文档。
2. **浏览器 accessibility smoke。** 对构建后的首页、一个英文指南和对应中文页面运行
   Playwright + axe。静态 contrast 与 link 检查无法证明键盘导航、landmark、ARIA、移动
   菜单、搜索、语言切换或 copy-button 行为。
3. **定时外部链接检查。** 使用有界 timeout、retry 和 allowlist，作为不阻塞 PR 的定时
   workflow；确定性本地 link checker 有意跳过其他 origin。
4. **部署后 Pages smoke。** 部署后请求真实 base URL、代表性英文与中文页面、`llms.txt`，
   以及一个必须直接返回 404、不得重定向的代表性未知/非规范路由。Build 成功并不能证明
   已部署 origin 和 base-path routing 可访问。

## Release gate

Release workflow 从精确 revision 重新构建，运行确定性 Python、TUI、Protocol、audit
和 packaging gate，在 Ubuntu 上构建唯一 release bundle，再在 Windows 与 macOS 上
重新验证下载后的 artifact。只有通过所有非特权 job 的 tag run 才能进入 attestation。

从 `main` manual dispatch 时，还要求精确 `GITHUB_SHA` 上由 GitHub Actions 生成的最新
`Required` 与 `Security required` check-run 都成功。它生成的 artifact 会在允许打 tag 前
通过 loopback 在三台真实支持 host 上运行 installer smoke。缺少该证据时只能合并
candidate，不能 tag 或 release。Tag workflow 会重新 build，并通过三个待发布 asset hash
与 tag 前 artifact 做 byte 比较；只要任一项不同，就必须在发布前针对 tag artifact 重跑
三端实机 loopback smoke。命令与发布后的 rollout recheck 见[发布](release.zh-CN.md)。

本地 release-quality gate 为：

```powershell
uv sync --locked --extra memory --dev
uv run ruff format --check src tests scripts
uv run ruff check src tests scripts
uv run mypy src tests scripts
uv run --extra memory pytest -q tests
uv run python scripts/generate_protocol_fixtures.py --check
uv lock --check
uv build --wheel --no-build-isolation

npm --prefix tui ci
npm --prefix tui run version:check
npm --prefix tui run format:check
npm --prefix tui run lint
npm --prefix tui run typecheck
npm --prefix tui test
npm --prefix tui run build
npm --prefix tui audit --package-lock-only --audit-level=high
npm pack ./tui --dry-run
```

依赖审计和 release bundle 校验详见[发布](release.zh-CN.md)。

## Live release 证据

在临时环境中使用全新凭据：

```powershell
$env:AWESOME_RUN_EXTERNAL = "1"
uv run --extra memory pytest -q tests/external/test_release_services.py
Remove-Item Env:AWESOME_RUN_EXTERNAL, Env:DEEPSEEK_API_KEY, `
  Env:MOONSHOT_API_KEY, Env:MEM0_API_KEY -ErrorAction SilentlyContinue
```

只记录 provider/service、status、duration 和脱敏诊断码。如果凭据、网络或平台不可用，
应说明缺失证据与残余风险，而不是报告成功。

## 失败分类

1. 阅读第一个失败的较低 gate 及其确切命令。
2. 复现该 job 的 lockfile、OS、Node/Python 版本与环境。
3. 判断失败属于代码、契约漂移、生成文件漂移、打包、平台行为、外部授权还是基础设施。
4. 修复负责的层；不要削弱测试或 aggregator。
5. 重新运行聚焦失败，再运行下一个更广 gate。

对于 GitHub Actions，编辑代码前先查看实际 log。Maintainer approval、label gate、quota
耗尽或服务不可用都不是产品测试失败，但仍意味着不能声称 required check 已通过。
