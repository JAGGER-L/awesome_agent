# 发布

一次 release 由一个经过审查的源代码 revision、一个产品版本、一次 bundle build、针对同一
artifact 的跨平台验证，以及与已发布 asset 匹配的 checksum 与 provenance 组成。在每个
平台重新 build 可以测试源代码可复现性，却不能证明用户收到的 byte 就是别处已经验证的
byte。

## Release 不变量

- `VERSION` 是唯一手工维护的产品版本来源。
- `contract-versions.json` 是相互独立的公共契约版本的唯一手工维护目录；它会为两种
  语言的 runtime consumer 生成精确、无依赖的 Python 与 TypeScript 投影。
- Python metadata、TUI package/lock/generated source、Protocol fixture manifest、
  installer、archive name 与内嵌 payload 都与 `VERSION` 一致；bundle 内的
  compatibility manifest 则把该版本与精确的 contract catalog 组合起来。
- 根目录、Python wheel 与 TUI 的 package metadata 和 license 文件都声明并携带同一份
  MIT license。授权正文必须精确一致；只有单独一行 copyright 可以变化。
- Release revision 位于 `main`，且精确 `GITHUB_SHA` 上由 GitHub Actions 生成的最新
  `Required` 与 `Security required` check-run 都已成功。
- 确定性测试不需要 live credential 或网络服务。
- Bundle 只在 Ubuntu build 一次，下载后的相同 byte 在 Windows 与 macOS 验证。
- `SHA256SUMS` 恰好覆盖三个已发布 executable/archive asset。
- 只有非特权平台验证通过后才 attest tag provenance。
- 已发布 tag 或 asset 绝不静默移动或替换；修正使用新版本。

## Artifact 的职责

`scripts/release/build_bundle.py` 恰好创建四个文件：

```text
dist/release/
  install.sh
  install.ps1
  awesome-<version>.zip
  SHA256SUMS
```

Archive 包含唯一、确定性的顶层目录，其中有：

- `VERSION`；
- `compatibility.json`，用于记录相互独立的公共契约所组成的规范 release identity；
- 根目录 MIT `LICENSE`；
- 已校验的 pure-Python Awesome wheel；
- 一份精确、带 SHA-256 hash 的生产 requirements lock；
- 构建后的 TUI entry point、package metadata、npm lock 与匹配的 license。

Installer 与 archive 一同发布，因为单行安装 URL 直接指向这些 asset。它们会校验 checksum，
并在产品安装根目录下安装私有 Python/Node runtime；它们不是 source-checkout 开发脚本。
安装根目录与负责配置、凭据、状态和 Memory 的 `AWESOME_HOME` 不同。默认安装根目录在
POSIX 上是 `~/.local/share/awesome`，在 Windows 上是
`%LOCALAPPDATA%\Programs\Awesome`。

公共 bootstrap asset 通过 `latest` 获取，但其中内嵌的 `VERSION` 必须把每个应用 payload
request 绑定到 `releases/download/v<version>`。两个 installer 都从 staging 前 recovery 到最终
清理全程持有唯一排他锁，在 install root 下 staging 以保证同文件系统 rename，并且只有应用与
launcher 都替换完成后，才通过删除 `.install-transaction` 提交。`app.rollback` 是 recovery
状态，不是第二个已安装版本。

## 1. 选择并准备版本

选择新的 semantic version。不要复用已有 Git tag 或 GitHub Release 版本。

1. 将 `VERSION` 更新为准确的 `MAJOR.MINOR.PATCH` 加一个换行。
2. 同步 TUI 管理的副本：

   ```powershell
   npm --prefix tui run version:sync
   ```

3. 如果本次 release 改变了序列化契约，更新 `contract-versions.json`，并用下面第一条
   命令重新生成语言 binding。每次 release 都用第二条命令检查已提交 binding 是最新的：

   ```powershell
   uv run python scripts/release/contract_versions.py --write
   uv run python scripts/release/contract_versions.py
   ```

4. 更新根目录两个 installer 中的版本常量。
5. 重新生成 Protocol fixture，使 manifest 记录产品版本：

   ```powershell
   uv run python scripts/generate_protocol_fixtures.py
   ```

6. 获取仓库 tag，证明 candidate 版本尚未使用，并验证所有版本与 license 表面一致：

   ```powershell
   git fetch --tags origin
   uv run python scripts/release/check_identity.py --tag-policy absent
   ```

   此脚本刻意读取本地 Git ref，而不调用 GitHub。CI 会在调用前 checkout 完整 tag history，
   因此开发者 checkout 必须先 fetch tag。确定性的 PR CI 无法证明一个没有对应本地 tag 的
   GitHub Release 不存在；发布前仍需检查 Releases 页面，并在仓库设置中保护版本 tag
   namespace。
7. 从已接受变更准备 GitHub Release note，包括用户可见行为、安全边界、配置/状态兼容性
   和已知限制。
8. 检查每一处版本相关 diff。Feature branch 不应包含意外版本变更。

Protocol version 与 Application schema version 相互独立。仅在线缆不兼容变更时递增
Protocol；只有持久化语义不能安全读取时才递增 Application schema。两者都不能取代唯一
产品版本。

因此，release identity 是一组 tuple，而不是重复使用同一个数字。
`contract-versions.json` 管理独立演进的标识，`VERSION` 管理产品 release。Builder 会把
两者组合成 `compatibility.json`，其中包含 Protocol 与 event-envelope 版本、Application
diagnostic-log 版本、Application schema current/migration-floor、user/workspace/UI 配置
可读取版本的精确集合、headless JSON identity 和 Thread export identity。Release
review 校验这组 tuple，而不会强制这些值相等。

## 2. 运行确定性 release gate

使用[测试](testing.zh-CN.md)中的完整 gate。至少包括 locked dependency setup、Ruff、
strict mypy、所有 Python test、Protocol fixture check、TUI format/lint/typecheck/test/
build、lock check、audit、wheel build 和 npm pack dry-run。

从精确 export graph 运行 Python 依赖审计：

```powershell
$Requirements = Join-Path ([System.IO.Path]::GetTempPath()) `
  "awesome-agent-release-requirements-$PID.txt"
try {
  uv export --locked --extra memory --no-dev --no-emit-project `
    --format requirements.txt --output-file $Requirements
  uv run pip-audit --require-hashes --progress-spinner off `
    --vulnerability-service pypi --requirement $Requirements
  uv run pip-audit --require-hashes --disable-pip --progress-spinner off `
    --vulnerability-service osv --requirement $Requirements
} finally {
  Remove-Item -LiteralPath $Requirements -ErrorAction SilentlyContinue
}
```

第一条命令让 pip-audit 通过隔离 pip 路径校验导出 hash。OSV 命令是在已经校验的精确
graph 上补充 advisory coverage；其中的 `--disable-pip` 不能替代第一次检查。

同时审计 npm lock：

```powershell
npm --prefix tui audit --package-lock-only --audit-level=high
```

Advisory 必须有明确决策：更新、约束、记录它为何不影响发布路径，或停止发布。没有经过
审查的仓库 policy，不得抑制 gate。

## 3. 本地 build 与验证

在目标 release revision 的干净 checkout 中运行：

```powershell
uv run python scripts/release/build_bundle.py
$Version = (Get-Content VERSION -Raw).Trim()
uv run python scripts/release/verify_bundle.py `
  "dist/release/awesome-$Version.zip" $Version
```

Builder 会从精确 Git commit time 派生 `SOURCE_DATE_EPOCH`，创建 wheel、导出 hashed
requirement、检查版本一致性、生成的 contract binding 与 TUI output，把 `VERSION` 和
contract catalog 组合起来，拒绝禁止内容、组装确定性 ZIP、复制 installer，并写入
checksum。Packaging test 证明 Hatch 在同一个 source epoch 下会生成相同 wheel byte；
两个 CI run 之间的最终依据仍是 asset checksum 比较，而不是环境变量本身。

Verifier 检查：

- release-directory inventory 与所有 checksum；
- archive path safety、member inventory 与 payload version；
- closed、bounded、canonical 的 `compatibility.json`；其中声明的 Protocol 版本驱动已安装
  Core handshake，Application schema identity 驱动已安装 wheel 的 storage verification；
- wheel filename、metadata、compatibility、entry point、RECORD hash、import origin，
  以及不存在 editable 或非生产 content；
- 精确 hashed dependency requirement 与隔离安装；
- `uv pip check`、Core import 与 console entry point；
- 在全新 home 与 workspace 中运行已安装 wheel 的 Protocol v4 生命周期：
  `initialize` -> workspace trust -> `application.getState` -> `shutdown`；
- TUI package/version/entry point；
- Schema 8 bootstrap、floor-7 线性 `7 -> 8` Thread-lineage migration 的数据保留与
  rollback 证据、不兼容状态分类、独占 reset 所有权，以及保留 config、Skills 与 Memory。

验证必须在 build wheel 和解压后的 payload 上运行。Fallback 到 editable checkout 会证明
错误的 artifact，因此会被拒绝。
因此，Protocol 与 Application schema 字段具备可执行的 artifact probe。其余字段构成严格
的 release inventory：gate 会校验数据形状，并在存在 runtime binding 时校验对应生成投影，
但 verifier 不会声称已经对每种格式完成 runtime compatibility proof。

## 4. 收集可选 live 证据

使用全新凭据和稳定网络，运行显式 gate 的 DeepSeek、Kimi、Mem0 与 Tavily Search/Fetch 检查：

```powershell
$env:AWESOME_RUN_EXTERNAL = "1"
uv run --extra memory pytest -q tests/external/test_release_services.py
Remove-Item Env:AWESOME_RUN_EXTERNAL, Env:DEEPSEEK_API_KEY, `
  Env:MOONSHOT_API_KEY, Env:MEM0_API_KEY, Env:TAVILY_API_KEY `
  -ErrorAction SilentlyContinue
```

只记录 service、status、duration 和脱敏 diagnostic code。Live 证据补充确定性 adapter
测试；它不授权把 credential 提交到仓库。如果无法收集，应在 release decision 中说明缺口。

## 5. 合并发布准备

Merge 前：

1. 确保 branch 只包含已接受的 release 工作；
2. 确认 Required 与 Security aggregator 通过；
3. 确认没有未解决 review 或 merge conflict；
4. 校验 diff 不包含 secret、generated cache、debug output 或过期文档；
5. 将经过审查的精确 revision 合并到 `main`。

在最新、干净的 `main` 上重新运行本地 identity/fixture check。Required CI 使用
`absent-or-current`：已发布 revision 仍然有效，而后续有变化的 revision 必须选择新版本。

## 6. 构建 candidate 并收集 tag 前实机证据

等待 `main` push 上名为 `Required` 和 `Security required` 的 run 成功，再从 `main` 手动
dispatch `Release gate`。Workflow 只有只读 Checks 权限；除非精确 `GITHUB_SHA` 上由
GitHub Actions 生成的这两个同名最新 check-run 都成功，否则它拒绝 build。它还使用
`absent` tag policy，因此已占用的版本 tag（包括不能解析到 commit 的 tag）会 fail closed。
Manual dispatch 会上传 candidate artifact，但不会 attest provenance。

下载 `awesome-release-<commit>`，保留外层 artifact digest、四文件 inventory 与
`SHA256SUMS`，并在测试前校验其中三个条目。使用受信的静态服务器，只在 loopback 暴露该
artifact 目录；例如在单独 terminal 中运行：

```text
python -m http.server 8765 --bind 127.0.0.1 --directory <artifact-directory>
```

当前 release line 的人工真实主机 gate 只覆盖 Windows 11 x64。Linux 与 macOS 仍受支持，
并继续接受 hosted CI 和 nightly 覆盖；由于维护者当前没有可控的 WSL2 或 macOS 主机，缺少
对应实机证据会记录为残余风险，但不阻塞本次发布。Windows candidate installer 使用：

```powershell
$env:AWESOME_INSTALL_CANDIDATE = "1"
$env:AWESOME_INSTALL_CANDIDATE_ASSET_BASE = "http://127.0.0.1:8765"
try {
  & .\install.ps1
} finally {
  Remove-Item Env:AWESOME_INSTALL_CANDIDATE, `
    Env:AWESOME_INSTALL_CANDIDATE_ASSET_BASE -ErrorAction SilentlyContinue
}
```

Candidate mode 是 release 测试 hook，不是备用下载功能。只有显式启用时才接受，并且只允许
没有嵌套 path、credential、query 或 fragment 的
`http://127.0.0.1:<1..65535>`；正常 installer 会拒绝 override。Windows 还要求 client
workstation（`ProductType == 1`），因此 Windows Server hosted runner 不能冒充 Windows 11
证据。

收集 host 证据前，在 Windows PowerShell 5.1 与当前受支持 PowerShell 下运行可执行的 Windows
installer contract harness。Fault injection 必须覆盖旧版本与首次安装 rollback、每一种
marker/rollback recovery 形态、延迟的 commit 后清理、同根 staging、原子 launcher
replacement、活动/崩溃锁、由确定性 barrier 分隔的两个 stale-lock reclaim contender，以及
保持外部 sentinel 不变的 link/reparse path。Portable `sh` harness 仍是 Ubuntu Required CI
中的自动化契约，不属于人工真实主机证据。

在 Windows 11 x64 主机上验证：

```text
candidate installer succeeds from the loopback-served artifact
awesome --version == VERSION
workspace starts and trust prompt is correct
provider configuration is visible without exposing a key
one simple Turn completes
one read-only command and one approved edit behave correctly
close/restart and --continue restore the expected Thread
```

使用一次性 OS user 或 VM snapshot 与临时 workspace；安装会修改产品 install root，也可能
更新用户 PATH 或 shell profile。停止 loopback server，并记录 host OS/architecture、commit
SHA、artifact checksum、命令与脱敏结果。该 Windows gate 与自动化的 Required、Security、
Release-gate 平台检查均通过后，candidate 才有资格打 tag。Release 残余风险必须明确记录
WSL2 与 Apple Silicon macOS 未进行人工真实主机验证。

## 7. 打 tag 并验证 CI artifact

创建与 `VERSION` 精确匹配的 annotated 或 lightweight version tag：

```powershell
$Version = (Get-Content VERSION -Raw).Trim()
git tag "v$Version"
git push origin "v$Version"
```

`Release gate` workflow 会再次校验精确 `GITHUB_SHA` 上的 Required/Security check-run，
校验 tagged commit 是 `origin/main` 的 ancestor、tag 恰好为 `v<version>`，且该 tag
解析到当前 checkout commit。然后它会：

1. 安装锁定 Python 与 TUI 依赖；
2. 重新运行确定性 release check 与 audit；
3. 在 Ubuntu 上只 build 并验证一次 bundle；
4. 上传包含四个文件的 artifact；
5. 在 Windows 与 macOS 下载并验证同一个 artifact；
6. 进入受保护的 `release` environment；
7. 重新检查 `SHA256SUMS`，为三个有 checksum 的 subject 创建 GitHub artifact
   attestation。

Windows 与 macOS 不重新 build。每个平台 verifier 都会安装下载到的 wheel 并执行同一条
Protocol v4 生命周期，因此它证明 packaged Core 能在各 CI runtime 启动，而不只是能在
Ubuntu import。任一平台 verifier 失败都会使 candidate 无效。

Hosted runner 验证与最终用户 host 的 installer 证据回答不同问题。前者在公共 asset 尚未
存在时验证 candidate bundle；它不能证明已发布的一行 installer 或特定最终用户环境。
本次 release 仅在 Windows 11 x64 收集人工证据；不得把自动化 Ubuntu/macOS 结果重新标记为
WSL2 或 Apple Silicon 实机证据。

发布前下载成功的 tag artifact，将 `SHA256SUMS` 中全部三个条目与已批准的 tag 前 candidate
比较。如果每个 asset hash 都完全相同，Windows 实机证据可用于 tagged byte；如果任一 hash
不同，必须针对 tag artifact 重新执行第 6 节完整的 Windows loopback smoke，之后才能发布。
`SOURCE_DATE_EPOCH` 消除了已知的 wheel timestamp 变化，但 checksum 相等才是证明；不能
仅因 source SHA 相同就推断 byte 相同。

## 8. 发布 GitHub Release

Workflow 不会自动创建 GitHub Release。Tagged workflow 与 attestation 成功后：

1. 从成功 workflow 下载 `awesome-release-<commit>`；
2. 在本地再次校验 `SHA256SUMS`；
3. 在现有 tag 上创建 **draft** GitHub Release `v<version>`，并粘贴经过审查的 release note；
4. 精确上传该 workflow artifact 中的 `install.sh`、`install.ps1`、
   `awesome-<version>.zip` 和 `SHA256SUMS`；
5. 保持 draft，先将远程名称、大小和全部三个 SHA-256 值与已验证 artifact 比较，并校验
   attestation 指向相同 subject；
6. 将其发布为稳定、非 prerelease 的 Release。该 publication event 是 GitHub Pages 文档站
   唯一的源代码部署触发器。

CI 验证与上传之间，不要 rebuild、编辑、重新压缩或重新生成任何 asset。

## 9. Rollout recheck

发布后，先等待 Release 触发的 Docs site workflow，并验证公共 base URL、代表性英文与中文
页面、`llms.txt`，以及必须返回 404 的非规范路由。随后在 Windows 11 x64 上使用文档中的
公共 one-line installer，重新检查 GitHub Release routing、公共 asset 名称、checksum 与
startup。这是 rollout recheck，不能替代 tag 前 Windows gate。

验证：

```text
one-line installer succeeds
awesome --version == VERSION
workspace starts and trust prompt is correct
provider configuration is visible without exposing a key
one simple Turn completes
one read-only command and one approved edit behave correctly
close/restart and --continue restore the expected Thread
```

使用一次性 user 或 VM snapshot 与临时 workspace。绝不要把破坏性测试清理指向用户
home、仓库根或未解析的环境变量。Rollout recheck 失败时执行下方 defective-release
流程；不得静默替换已发布 asset。

## 源代码外的仓库控制

维护者必须另行确认：

- branch rule 要求 `Required` 与 `Security required`；
- version-tag namespace 受到移动/删除保护；
- `release` environment 只允许 version tag，并要求预期 reviewer policy；
- Action 只限经过审查的 allowlist；
- GitHub Dependency Graph 与 Dependabot 已启用；
- secret scanning 与 push protection 已启用；
- GitHub Pages deployment environment 允许受保护的 version tag，且权限正确。

仅靠 workflow 文件无法证明这些设置。对于单维护者，rule 可以允许显式 administrative
break-glass 路径，但普通 merge/release 仍应等待 required check。

## 失败或有缺陷的 release

如果 tag workflow 失败，在新 commit 上修复源代码；一旦任意公共 tag 或 asset 已存在，
就要选择新版本。绝不要移动已发布 tag，让旧名称指向不同代码。

如果 release 发布后存在严重缺陷：

1. 清楚标记 release 与 note；
2. 停止推荐其 installer；
3. 评估 credential、state-schema 和 workspace 影响；
4. 通过同一完整 gate 准备更高修复版本；
5. 发布 recovery 或 upgrade guidance，不要求用户盲目删除本地状态。

跨越更新 Application schema 降级 binary 不是受支持的恢复技术。旧 binary 会对新状态
fail closed。

## Release 记录

在 GitHub Release 或维护者 handoff 中保留：

- version、tag 与 commit SHA；
- Required/Security/Release gate run link；
- artifact attestation 与 checksum；
- 确定性和可选 live 证据；
- tag 前 Windows candidate result、tagged-asset checksum 比较、必要的 tagged-asset smoke
  重跑，以及 rollout recheck；
- 未验证证据与残余风险；
- 生成的 compatibility-manifest tuple，以及 state/Protocol 兼容说明；
- 精确已发布 asset inventory。

不要包含 secret value、私有机器 path、原始 provider response 或无界 CI log。
