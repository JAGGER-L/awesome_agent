# Skills

Skill 是一组具名、可复用的指令，可选择附带文本资源。Skills 最适合审查、调试、测试或
仓库专属发布清单等可重复流程。它们属于上下文，不是可执行插件，也不能授予工具权限。

## 来源与优先级

Workspace 获得信任后，Awesome 会发现三类来源：

| 来源 | 位置 | 通常的所有者 | Session 身份固定 |
| --- | --- | --- | --- |
| Bundled | 已安装的 Python 包内部 | Awesome release | 是 |
| User | `<AWESOME_HOME>/skills/<name>/` | 当前操作系统用户 | 是 |
| Workspace | `<workspace>/.awesome/skills/<name>/` | Repository/workspace | 是，并包含受信任 Workspace 链 |

发现顺序依次为 bundled、user、workspace。后出现的同名包会遮蔽更早的包并产生诊断，因此
有效优先级为 workspace > user > bundled。在 user 或 workspace 配置中禁用某个名称，都会
将该名称从有效 catalog 中移除。

当前 bundled catalog 包含 `debug`、`git-workflow`、`review` 和 `test`。请把它视为具体
release 的细节；对于正在运行的安装，`/skills` 才是权威 catalog。

## 创建 Skill

创建名称与 frontmatter `name` 一致的目录，然后添加 `SKILL.md`：

```text
<AWESOME_HOME>/skills/review-api/
|-- SKILL.md
`-- references/
    `-- checklist.md
```

下面是一个完整、有效的示例：

```markdown
---
name: review-api
description: Review an HTTP API change for compatibility and operational risk
allowed-tools: [ls, read_file, glob, grep]
license: MIT
compatibility: Awesome Agent 1.3.x
metadata:
  owner: platform-team
  maturity: stable
---
# Review an API change

Start from the public request and response contract. Inspect callers before
implementations. Report breaking behavior, authorization mistakes, error-shape
changes, missing cancellation, and missing tests.

Read `references/checklist.md` only when the change exposes an HTTP endpoint.
```

支持的 frontmatter 键是精确集合；未知字段会使包无效。下表给出规范的编写类型：

| 字段 | 必需 | 契约 |
| --- | --- | --- |
| `name` | 是 | 以小写字母开头，后接至多 63 个小写字母、数字或连字符；必须与目录名完全相同 |
| `description` | 是 | 1–500 个字符 |
| `allowed-tools` | 否 | 一个字符串，或最多 128 个匹配 `[a-z][a-z0-9_.-]{0,199}` 的唯一工具名称列表；仅为说明性元数据 |
| `license` | 否 | 字符串，最多 500 个字符 |
| `compatibility` | 否 | 字符串，最多 500 个字符 |
| `metadata` | 否 | 值与 JSON 兼容的映射 |

当前 parser 仍会用 `str()` 规范化若干非工具标量字段，因此数字 description 可能作为文本
被接受。不要依赖这种强制转换；请按示例使用字符串。`allowed-tools` 还会受到数量、去重和
名称校验，避免格式错误的 metadata 膨胀或破坏自动 catalog。

文件必须以 `---` YAML frontmatter 开头。YAML parser 限制为 64 层、4,096 个节点和
64 个 alias；递归 alias 无效。`SKILL.md` 必须是 UTF-8、非二进制，且不大于 1 MiB。

正文应使用祈使语气并且可测试：说明 Skill 何时适用、所需输入、有序工作步骤、停止条件、
验证和预期输出。将大段背景材料放入资源，以便只在需要时加载。

## 选择和加载 Skills

```text
/skills                 # inspect the catalog and choose a mode
/skills auto
/skills off
/skills review-api
```

选择存储在当前 Thread 上，并应用于后续 Turn。三种模式具有不同且封闭的行为：

| 模式 | 冻结上下文 | 模型可见的 Skill 工具 |
| --- | --- | --- |
| `auto` | 最多 64 个有效 Skill 的确定性 catalog，并限制为 32 KiB 和 4,096 个估算 token | `load_skill`、`read_skill_resource` |
| `off` | 不包含 Skill catalog 或正文 | 无 |
| `<name>` | 该 Skill 正文中最多 5,000 个估算 token，作为强制 system context | 仅可对该 Skill 使用 `read_skill_resource` |

为限制 Turn 准备的工作量，`auto` 会先按名称确定性排序，仅从前 256 个有效 Skill 候选中选择
最终的 64 个。如果仍有后续候选，或某个候选因字节或 token 上限被排除，catalog 会将自身标记
为不完整。

`auto` 允许模型从有界 catalog 中选择，但不会静默运行 Skill。`off` 是运行时隔离边界：即使
伪造调用，也会在权限策略和 handler 之前被硬准入拒绝。工具参数 object 严格且封闭，因此
未知字段和用非字符串标量替代字符串的输入会作为 `invalid_arguments` 被拒绝。

```yaml
version: 2
skills:
  disabled:
    - review-api
```

受信任 Workspace 配置中也有相同的 `skills.disabled` 字段。两个作用域中的禁用名称会合并。
Core 启动时执行发现；添加、替换或移除包后请重启 Awesome。

## 内部加载链

```text
discover directories
  -> parse bounded frontmatter
  -> resolve shadowing + disabled names
  -> pin package + SKILL.md identity
  -> immutable session catalog + diagnostics
  -> freeze selected identities in Turn context/checkpoint
  -> expose tools allowed by the frozen Skill mode
  -> strip frontmatter + 5,000-token bound
  -> Context Builder
```

Skill 加载时，`allowed-tools` 会被冻结并作为诊断元数据返回。它不会过滤模型的工具 catalog，也不会
绕过或收紧[权限策略](../reference/permission-modes.zh-CN.md)。可以在正文中说明对工具的预期，
但真正的权限必须在 Tool Executor 边界强制执行。

Skill 读取使用内置 `context.read` capability。只有硬准入证明请求的包和操作位于该 Turn
冻结的 Skill scope 中后，permission mode 才会无提示允许该 capability。Runtime 重建、恢复
或磁盘上的包变化都不能扩大此 scope；identity 不匹配会返回 `conflict`。包变更只在新 Session
中生效。

资源使用相对于包的路径，拒绝绝对路径和 `..`，并且必须是 UTF-8 非二进制文本且不大于
1 MiB。`read_skill_resource` 每次调用最多返回 5,000 个估算 token，并报告是否发生截断。

## Skill 身份与 Workspace 安全

Workspace 包由仓库控制，因此发现流程从受信任 workspace anchor 开始，并验证
`.awesome/skills`、包目录和 `SKILL.md` 的每个组成部分。符号链接、junction 及其他
reparse point 会在内容被接受前遭到拒绝。

每个 catalog 条目都有版本化 identity，由规范化 descriptor metadata、发现时 `SKILL.md`
fingerprint 与内容派生。后续准入和 handler 读取会以 no-follow 方式重新打开包，并要求 identity
一致。Workspace 条目还记录 workspace anchor 到包目录的每一级目录身份。因此，替换包或
`SKILL.md`，以及替换 Workspace Skill 的 workspace 链，都会安全失败（fail closed）。

读取资源时会重新验证已固定链和包含关系，拒绝包下的每一个 symlink/junction/reparse
组成部分，并对被读取文件使用 `lstat`/open/`fstat` 身份检查。它**不会**在发现时固定普通
嵌套资源目录或资源文件的内容。读取前已安全完成的非 reparse 资源替换可能会被观察到；
嵌套 reparse point 或在受检查 open 过程中发生的替换会安全失败。这一区分既防止路径
重定向和检查/打开竞态，也没有宣称完整的包是不可变内容快照。

Bundled 与 User 包使用相同的 package/`SKILL.md` identity 要求；Workspace 包额外使用更强的
受信任 anchor 链。所有资源遍历都会拒绝逃逸与 symlink/reparse 组成部分。

## 诊断与恢复

一个无效包会产生一条 catalog 诊断，但不会隐藏有效包。`/skills` 会用有界的来源信息报告
`invalid_skill`、`unsafe_workspace_skill_path`、`disabled` 和 `shadowed` 条件。

Skill 失败时：

1. 运行 `/skills`，识别来源、有效名称和诊断。
2. 确认目录名等于 frontmatter `name`，并移除未知字段。
3. 检查 UTF-8、大小和 link/reparse 边界。
4. 重启 Awesome 以创建新的 catalog 快照。
5. 显式选择该 Skill，并检查 `/context` 以确认其已包含。

如果内容是应独立于流程演进的事实，请改用 [Memory](memory.zh-CN.md)。只有在流程需要新的外部
能力，而不是 Awesome 现有工具时，才使用 [MCP](mcp.zh-CN.md)。
