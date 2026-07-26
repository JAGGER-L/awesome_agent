# 发布

一次 release 由一个经过审查的源代码 revision、一个产品版本、一次 bundle build、针对同一
artifact 的跨平台验证，以及与已发布 asset 匹配的 checksum 与 provenance 组成。在每个
平台重新 build 可以测试源代码可复现性，却不能证明用户收到的 byte 就是别处已经验证的
byte。

## Release 不变量

- `VERSION` 是唯一手工维护的产品版本来源。
- Python metadata、TUI package/lock/generated source、Protocol fixture manifest、
  installer、archive name 与内嵌 payload 都与 `VERSION` 一致。
- Release revision 位于 `main`，并已通过 Required 与 Security check。
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
- 已校验的 pure-Python Awesome wheel；
- 一份精确、带 SHA-256 hash 的生产 requirements lock；
- 构建后的 TUI entry point、package metadata、npm lock 与 license。

Installer 与 archive 一同发布，因为单行安装 URL 直接指向这些 asset。它们会校验 checksum，
并在产品安装根目录下安装私有 Python/Node runtime；它们不是 source-checkout 开发脚本。
安装根目录与负责配置、凭据、状态和 Memory 的 `AWESOME_HOME` 不同。默认安装根目录在
POSIX 上是 `~/.local/share/awesome`，在 Windows 上是
`%LOCALAPPDATA%\Programs\Awesome`。

## 1. 选择并准备版本

选择新的 semantic version。不要复用已有 Git tag 或 GitHub Release 版本。

1. 将 `VERSION` 更新为准确的 `MAJOR.MINOR.PATCH` 加一个换行。
2. 同步 TUI 管理的副本：

   ```powershell
   npm --prefix tui run version:sync
   ```

3. 更新根目录两个 installer 中的版本常量。
4. 重新生成 Protocol fixture，使 manifest 记录产品版本：

   ```powershell
   uv run python scripts/generate_protocol_fixtures.py
   ```

5. 从已接受变更准备 GitHub Release note，包括用户可见行为、安全边界、配置/状态兼容性
   和已知限制。
6. 检查每一处版本相关 diff。Feature branch 不应包含意外版本变更。

Protocol version 与 Application schema version 相互独立。仅在线缆不兼容变更时递增
Protocol；只有持久化语义不能安全读取时才递增 Application schema。两者都不能取代唯一
产品版本。

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

Builder 自己会创建 wheel、导出 hashed requirement、检查版本一致性与 TUI output、拒绝
禁止内容、组装确定性 ZIP、复制 installer，并写入 checksum。

Verifier 检查：

- release-directory inventory 与所有 checksum；
- archive path safety、member inventory 与 payload version；
- wheel filename、metadata、compatibility、entry point、RECORD hash、import origin，
  以及不存在 editable/migration content；
- 精确 hashed dependency requirement 与隔离安装；
- `uv pip check`、Core import 与 console entry point；
- TUI package/version/entry point；
- 当前 storage bootstrap、不兼容状态分类、独占 reset 所有权，以及保留 config、Skills
  与 Memory。

验证必须在 build wheel 和解压后的 payload 上运行。Fallback 到 editable checkout 会证明
错误的 artifact，因此会被拒绝。

## 4. 收集可选 live 证据

使用全新凭据和稳定网络，运行显式 gate 的 DeepSeek、Kimi 与 Mem0 检查：

```powershell
$env:AWESOME_RUN_EXTERNAL = "1"
uv run --extra memory pytest -q tests/external/test_release_services.py
Remove-Item Env:AWESOME_RUN_EXTERNAL, Env:DEEPSEEK_API_KEY, `
  Env:MOONSHOT_API_KEY, Env:MEM0_API_KEY -ErrorAction SilentlyContinue
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

在最新、干净的 `main` 上重新运行本地 identity/fixture check。手动 dispatch `Release gate`
workflow 只允许来自 `main`；它会 build 并上传 candidate，但不会 attest provenance。

## 6. 打 tag 并验证 CI artifact

创建与 `VERSION` 精确匹配的 annotated 或 lightweight version tag：

```powershell
$Version = (Get-Content VERSION -Raw).Trim()
git tag "v$Version"
git push origin "v$Version"
```

`Release gate` workflow 会校验 tagged commit 是 `origin/main` 的 ancestor，且 tag 恰好为
`v<version>`。然后它会：

1. 安装锁定 Python 与 TUI 依赖；
2. 重新运行确定性 release check 与 audit；
3. 在 Ubuntu 上只 build 并验证一次 bundle；
4. 上传包含四个文件的 artifact；
5. 在 Windows 与 macOS 下载并验证同一个 artifact；
6. 进入受保护的 `release` environment；
7. 重新检查 `SHA256SUMS`，为三个有 checksum 的 subject 创建 GitHub artifact
   attestation。

Windows 与 macOS 不重新 build。任一平台 verifier 失败都会使 candidate 无效。

## 7. 发布 GitHub Release

Workflow 不会自动创建 GitHub Release。Tagged workflow 与 attestation 成功后：

1. 从成功 workflow 下载 `awesome-release-<commit>`；
2. 在本地再次校验 `SHA256SUMS`；
3. 在现有 tag 上创建 GitHub Release `v<version>`；
4. 粘贴经过审查的 release note；
5. 精确上传该 workflow artifact 中的 `install.sh`、`install.ps1`、
   `awesome-<version>.zip` 和 `SHA256SUMS`；
6. 将远程名称、大小和全部三个 SHA-256 值与已验证 artifact 比较；
7. 校验已发布 attestation 指向相同 subject。

CI 验证与上传之间，不要 rebuild、编辑、重新压缩或重新生成任何 asset。

## 8. 安装 smoke test

发布后关闭现有 Awesome 进程，并在需要 release 证据的每个支持 host 上测试文档中的
installer。

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

安装测试应使用临时 home 与 workspace。绝不要把破坏性测试清理指向用户 home、仓库根或
未解析的环境变量。

## 源代码外的仓库控制

维护者必须另行确认：

- branch rule 要求 `Required` 与 `Security required`；
- version-tag namespace 受到移动/删除保护；
- `release` environment 只允许 version tag，并要求预期 reviewer policy；
- Action 只限经过审查的 allowlist；
- GitHub Dependency Graph 与 Dependabot 已启用；
- secret scanning 与 push protection 已启用；
- GitHub Pages deployment environment 与权限正确。

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
- 支持平台 smoke result；
- 未验证证据与残余风险；
- state/protocol 兼容说明；
- 精确已发布 asset inventory。

不要包含 secret value、私有机器 path、原始 provider response 或无界 CI log。
