# 契约与文档

Awesome 使用可执行契约保持 Python Core、Ink、存储、打包和文档一致。契约不只是公共
API：package 所有权、唯一 graph compiler、命令 inventory、schema identity、事件顺序
和文档导航都会被有意检查。

## 契约层级

当来源不一致时，按以下顺序解决：

1. 当前仓库行为和已接受的产品需求；
2. 严格源代码契约及其聚焦测试；
3. 根目录 `ARCHITECTURE.zh-CN.md` 职责图；
4. 专题架构页面；
5. 用户指南和参考页面；
6. 对话、旧计划、历史 release note 或外部项目惯例。

该顺序不意味着测试不可变。经过审查的产品变更可以改变测试，但实现、跨边界 fixture、
文档和 migration/reset 行为必须一致地变化。

## 公共契约检查清单

当变更影响以下任一内容时，将其视为公共或跨边界变更：

- CLI 参数、启动、工作区选择或退出行为；
- 命令、interaction、审批选项或错误码；
- 配置 key、环境变量、优先级、默认值或限制；
- permission mode、hard denial、tool capability 或可逆性；
- model/provider catalog、stream event、usage 或 retry 语义；
- Tool 名称、schema、result、activity、timeout 或 cancellation；
- Protocol method、payload、event、optional/null 行为或版本；
- Thread/Turn record、Application schema、checkpoint 或 reset 边界；
- Skills、MCP、Memory 或工作区指令行为；
- package 依赖、entry point、wheel/npm/bundle inventory；
- 架构所有权、依赖方向或 framework 所有权；
- 文档 route、navigation、localization 或安装命令。

对于每个适用项，找出 Python 所有者、TypeScript 消费方、持久化形态、恢复行为、测试、
文档和 release verifier。

## 结构契约

`tests/structural/` 在不运行完整用户流的情况下检查架构。该 suite 当前保护：

- 精确 Python package 与 storage module inventory；
- 允许的内部 import edge；
- LangGraph、OpenAI SDK、MCP、jsonschema 与 SQLite 的所有者；
- 唯一 `StateGraph` compiler；
- `AgentState` 字段与 context invocation 形态；
- Application facade、command dispatcher 与 Thread replacement 所有权；
- built-in tool capability 与 Change Journal 独立性；
- 产品版本权威与 direct dependency；
- protocol/package/documentation inventory 和 Markdown link。

这些测试将高价值架构决策变成立即失败。如果新增 package、table、command、dependency
或 graph field，只有在说明架构为何改变之后才能更新测试。

## Protocol v3 变更工作流

Protocol fixture 是 Python/TypeScript 双向证据。若要变更 method、result、event、command
outcome 或 projection：

1. 更新严格 Python model 以及所属 facade/method/event 路径。
2. 向 fixture generator 添加有效边界示例和接近但无效的反例。
3. 重新生成 fixture：

   ```powershell
   uv run python scripts/generate_protocol_fixtures.py
   ```

4. 检查 `protocol/fixtures/v3/` 和 manifest hash；绝不手工编辑。
5. 更新 `tui/src/protocol/` 下严格 Zod schema。
6. 为权威状态变更更新 reducer/effect 代码。
7. 为可见事实更新 exhaustive Presenter/component。
8. 运行：

   ```powershell
   uv run python scripts/generate_protocol_fixtures.py --check
   uv run pytest -q tests/unit/protocol tests/e2e/test_stdio_product.py
   npm --prefix tui run typecheck
   npm --prefix tui test
   ```

未知字段仍然是错误。Optional 与 nullable 保持不同。请求整数保持在 JSON/JavaScript 安全
范围内。破坏 wire contract 需要新 protocol version；仅提升产品版本无法阻止同版本旧组件
完成握手。

## 命令契约

`application/commands.py` 中的 `CommandName` 和 `COMMAND_OWNERS` 是运行时权威。
当前 catalog 有 21 个 Application 命令和四个 Ink-local 命令：

```text
Application:
  new rename resume context compact auth model thinking workspace
  diff undo redo tools skills mcp memory status usage doctor config permissions

Ink:
  help theme copy quit
```

完整语法和行为见[斜杠命令参考](../reference/commands.zh-CN.md)。不要在贡献者文档中维护
第二份手工 registry。

变更命令时，校验：

- parser 与 completion 只插入可执行语法；
- owner inventory 与 Python fixture 和 TypeScript catalog 一致；
- Application 命令恰有一个 dispatcher handler；
- 没有斜杠命令提交隐藏的 Agent Turn；
- 返回唯一可辨识 result/interaction/error；
- 权威 effect 与纯 presentation 保持分离；
- empty、invalid、unavailable、busy 与 interaction 状态可见；
- foreground observation 分类显式；
- Help、Presenter、transcript 和聚焦 UI 测试一致。

活动 Operation 期间，只有 `/context`、`/workspace`、`/tools`、`/mcp`、
`/mcp status [id]`、`/status`、`/usage` 和 `/config` 是 Core observation。改变该集合
属于并发契约变更，测试必须覆盖两个竞态方向。

## 版本与 package 契约

`VERSION` 是唯一手工维护的产品版本。Python package、TUI package/lock/generated
source、installer、release archive 和 Protocol fixture manifest 必须一致。

运行 package 契约 gate：

```powershell
node tui/scripts/sync-version.mjs --check
uv lock --check
uv build --wheel --no-build-isolation
npm --prefix tui test -- tests/packaging/package.test.ts
npm --prefix tui run build
npm pack ./tui --dry-run
```

Packaging test 会 build、pack、安装并运行 tarball。最终 dry-run 应在全新 build 后执行，
只是可检查的内容视图；它本身不能证明 `dist` 是当前版本或已安装 bin 可以运行。

有意发布新版本时，编辑 `VERSION`，运行 version sync，更新两个 installer 与 release note，
再检查所有变更。不要通过放宽 packaging test 隐藏版本漂移。

Wheel 契约校验项目身份、metadata、pure-Python tag、entry point、RECORD hash、所需 package
member，以及不存在 editable/development artifact。TUI package 只包含 `dist`、README 和
license。Release bundle verifier 会在隔离环境中安装精确的 hashed dependency lock 与
wheel。

## 文档信息架构

文档按读者意图组织，而不是按源代码 package 组织：

```text
Start here -> Core concepts -> Use Awesome -> Extend Awesome
           -> Reference -> Architecture -> Contribute -> Project
```

- **Start here** 让新用户完成第一个成功 Turn。
- **Core concepts** 提供预测行为所需的心智模型。
- **Use Awesome** 提供面向任务的工作流与恢复指导。
- **Extend Awesome** 介绍受支持的 Memory、Skill 与 MCP 界面。
- **Reference** 提供完整、可搜索的语法、schema 和限制。
- **Architecture** 解释所有权、实现、失败和取舍。
- **Contribute** 将这些契约转化为开发与发布工作流。

这种分离防止 quickstart 变成源码构建手册，也防止 reference table 承担其无法解释的设计
理由。页面应该向前链接到读者的下一项任务，并横向链接到概念、参考和架构详情，而不是
重复整节内容。

### 信息架构依据

该结构借鉴当前官方 Agent 文档的组织模式，而非产品行为：Hermes 在其
[文档](https://hermes-agent.nousresearch.com/docs/)中使用显式学习路径，并分离用户、
开发者与参考资料；Codex 在其[最佳实践指南](https://learn.chatgpt.com/guides/best-practices)
和[安全文档](https://learn.chatgpt.com/docs/agent-approvals-security)中组织实践工作流、配置、
定制以及分层审批/安全主题；Claude Code 在其
[概览](https://code.claude.com/docs/en/overview)和
[权限指南](https://code.claude.com/docs/en/permissions)中区分概念、任务指南、参考，
以及权限与隔离的不同职责。这些只是组织输入，不是功能比较。关于 Awesome 的每项陈述
都必须以本仓库的源代码、测试、配置和 release 契约为准。

## 规范 Markdown 与站点生成

`docs/` 下的仓库 Markdown 是规范来源。`site/scripts/sync-content.mjs` 在 check/build
前重建 Starlight 内容：

- `docs/README.md` 是仓库文档索引，不生成站点页面；
- 目录 `README.md` 会变成该目录的 index route；
- 每个英文源都有一个完整的 `name.zh-CN.md` 对页，包括目录 `README.md`；翻译缺失或
  孤立时，sync 与 navigation check 会失败，不会生成 fallback 页面；
- 根目录 `ARCHITECTURE.md` 和 `ARCHITECTURE.zh-CN.md` 会成为配对的
  `architecture/overview` route；
- 源 heading 会变成生成的 title frontmatter；
- 缺失时会提供有界整句 description、规范来源更新时间和 edit URL；
- 指向 `.md` 文件的相对 Markdown link 会被改写为生成 route。

不要编辑 `site/src/content/docs/`；该目录由生成器维护。应根据需要编辑规范 Markdown、
seed homepage、navigation manifest、style/component 或 sync script。

`site/documentation-catalog.mjs` 会在同步写入生成内容之前，把每个源编译为唯一的 source
identity、locale、canonical route 与 output path。它读取 `site/docs-navigation.mjs`，要求
sidebar route 与规范英文源是精确集合，并拒绝 source、route 或 output collision。每个
英文源（包括只面向仓库的根目录 `README.md` 与 `docs/README.md`）都必须有一个完整
中文对页，两种语言都不能出现孤立页面。

`site/translation-lock.json` 记录全部 46 组中英文仓库文档与首页正文经过规范化的
SHA-256 identity。两个源都完成翻译和审查后，运行
`npm --prefix site run translations:lock` 并检查 lock
diff。英文或中文源发生变化而未更新经过审查的对页时，陈旧 hash 会在生成目录被替换前
中止同步。只更新 lock 不能代替翻译证据；同一契约还会检查正文语言、结构、可执行 fence、
外部 URL、inline identifier，以及中英文一致的同语言目标页面集合。首页 JSON 另有严格
schema、稳定 ID、共享 route map、结构 parity 与语言完整性检查。

页面合并或改名时，直接更新规范 route。Awesome 不保留文档 route alias 或 redirect；
旧 URL 与非规范 URL 按设计返回 404。

站点成功 build 后，`site/scripts/generate-llms.mjs` 会从同一 navigation manifest 和每个
规范页面 H1 派生 `dist/llms.txt`。它发布带 base-aware 公共 URL 的有序文档索引；这是
生成输出，不是第二份手工维护的索引。

构建站点 checker 会从同一 route set 派生精确契约：86 个规范 HTML 页面加一个 404、
恰好 86 个 sitemap URL，以及 `llms.txt` 中恰好 86 个 Markdown link。额外页面、重复
URL、redirect page、编码路径逃逸、没有 index 的目录、非普通输出节点，以及指向
`dist` 外的真实路径都会使检查失败。Markdown link 发现与改写共享同一棵 AST；构建后的
HTML 与 sitemap 使用语义解析器，不把注释或 script 中的伪标签当作证据。替换或写入生成
内容前还会逐组件检查路径，并拒绝任何 symlink、junction 或 reparse point。

规范输入上限为 1 MiB，必须是严格 UTF-8 且不含 NUL。读取器会验证仓库与每一层路径组件；
平台提供相应能力时使用 no-follow open，通过 `fstat` 将已打开 handle 与先前 `lstat`
identity 绑定，并在有界读取后再次检查 identity 与 metadata。输出文件使用同父目录中的
随机独占临时文件、handle identity 检查、`fsync` 与基于 rename 的安装。完整文档树先在
同父 staging 目录中生成，再通过 identity-bound rename/backup swap 安装，因此渲染失败时
旧树仍保持不变。cleanup 不会遍历 identity 已变化的对象。

这是 fail-closed 的构建完整性边界，不是 OS 隔离。Node 没有跨平台的
directory-handle-relative rename/unlink 或原子 directory exchange，因此实现不宣称能
消除同一用户恶意制造的最终 pathname syscall 理论竞态。检测到 identity 漂移时构建会
中止；系统可能保留随机临时对象或 backup 供检查，也不会冒险清理身份未知的对象。

Starlight 渲染生成文件，因此其默认 Git lookup 无法恢复规范 Markdown 的历史。同步过程
会读取每个源文件的最近 commit date 并注入 `lastUpdated`；docs CI 与 Pages job 使用完整
历史 checkout。没有 `.git` 的本地源代码 archive 会 fallback 到源文件 modification date。

## 页面契约

一个可持续的页面应根据需要回答：

1. 这个概念或工作流解决什么问题？
2. 读者最小正确路径是什么？
3. 为什么这样设计？
4. 内部哪个组件负责？
5. 哪些 input、output、limit 和 default 是精确值？
6. error、timeout、cancellation、concurrency 和 recovery 时会怎样？
7. 它不提供什么安全边界？
8. 哪些替代方案或取舍重要？
9. 读者下一步应该去哪？
10. 哪些源代码与测试证明架构陈述？

并非每个页面都需要每一节。Quickstart 应针对首次成功优化；reference 页面应针对完整性
和搜索优化；深入架构页面应包含所有权、数据流、失败语义、取舍和源代码/测试索引。

## 写作约定

- 始终使用精确产品名称 Workspace、Thread、Turn、Operation、ChangeSet、Skill、Memory、
  MCP、Core 和 TUI。
- 用现在时描述当前行为，并明确将 roadmap 行为标为未来工作。
- 优先提供可复制的精确命令和有界示例。
- 在 action 前说明安全默认值和破坏性后果。
- 将 approval/policy 与 sandbox/isolation 保证分开。
- 当 sequence、ownership 或 branching 用图更容易理解时，使用可移植文本图。如果引入
  rendered diagram format，应在同一次变更中加入固定版本的 build-time renderer 和站点
  校验；不要意外把未渲染图语言当作 code sample 发布。
- 图应足够小，便于在 terminal/GitHub 和 Pages 上阅读。
- 规范文档之间使用相对 `.md` link，源代码/测试路径使用反引号；站点生成器会改写前者。
- 链接到所属 reference，不要在多个页面复制长表格。
- 避免在 evergreen guide 中硬编码历史版本叙事。
- 每个英文和中文页面都要在行为上保持一致；两种语言都是必需的规范文档，不是 fallback
  variant。

## 文档验证

运行仓库和站点契约：

```powershell
git diff --check
uv run pytest -q tests/structural/test_markdown_links.py
npm ci --prefix site
npm --prefix site run check:contracts
npm --prefix site run check:navigation
npm --prefix site run check:contrast
npm --prefix site run check
$env:SITE_URL = "https://jagger-l.github.io"
$env:BASE_PATH = "/awesome_agent"
npm --prefix site run build
npm --prefix site run check:links
Remove-Item Env:SITE_URL, Env:BASE_PATH -ErrorAction SilentlyContinue
```

源 link 测试会捕获缺失文件。Catalog 与 navigation 校验会捕获陈旧翻译 hash、语言或
identifier 漂移、孤立页面、重复页面、collision、不存在 route 和未配对翻译。Theme
contrast 检查保护两种 palette 下小号文字的 WCAG AA 对比度。Astro 检查生成的
frontmatter/content。Production-base build 与精确 built-site scan 会捕获仅检查 source
时遗漏的 route、anchor、locale pair、output containment、sitemap、llms 和 asset 错误。

文档变更还应搜索被取代的名称和链接：

```powershell
rg -n "old-page-name|old-command|old-config-key" README.md README.zh-CN.md `
  ARCHITECTURE.md AGENTS.md docs site tests
```

## 文档审查检查清单

- [ ] 陈述以当前代码/测试为依据，而不是对话或竞品文档。
- [ ] 用户路径、设计理由、内部所有者、限制和失败清晰。
- [ ] 示例使用当前命令/配置和安全 placeholder。
- [ ] 破坏性或外部作用说明恢复边界。
- [ ] 新建/移动/删除页面已反映在两种语言的文档索引和导航中。
- [ ] 每个英文源都有完整中文对页，且两个 locale 都不存在孤立页面。
- [ ] 入站/出站链接和下一步阅读路径仍然连贯。
- [ ] 受影响时已更新根 README 中英文版本和 `AGENTS.md` 索引。
- [ ] 根架构仍是权威；专题页面没有重新定义它。
- [ ] 未提交生成的站点内容或本地 build output。
- [ ] Source link、navigation、Astro、production build 和 built link 全部通过。
