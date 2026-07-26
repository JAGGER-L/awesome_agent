# Memory

Memory 是可选的长期参考上下文。它适合保存稳定的用户偏好和持久的 Workspace 约定。
它不是对话历史、指令通道、秘密存储，也不能代替提交到仓库的项目文档。

前置条件是受信任的 Workspace；使用 Mem0 Cloud 时，还需要可选的 Memory 依赖和已选中的
Mem0 凭据。两种 Memory 后端默认都关闭，并且可以独立启用。

## 本地与云端 Memory

| 属性 | 本地文件 Memory | Mem0 Cloud |
| --- | --- | --- |
| 默认 | 关闭 | 关闭 |
| 作用域 | `user` 和 `workspace` | `user` 和 `workspace` |
| 存储 | `<AWESOME_HOME>` 下的 Markdown | Mem0，以不透明的 Awesome 身份标识为键 |
| Recall | 考虑每一份有效的本地文档 | 使用当前用户输入进行语义搜索 |
| 写入路径 | 显式命令或 Memory 工具 | 回答后的提炼；显式搜索/删除命令 |
| 条目限制 | 每条 2,000 个字符；每份文档 1,000,000 字节 | 每个候选项 500 个字符；每次提炼最多五个候选项 |
| 失败影响 | 对应作用域报告错误；不会静默改写文件 | Turn 带一条诊断继续执行 |

当事实应当保持可检查且仅留在本机时，选择本地 Memory。当跨大量事实的语义 recall 值得将
有界、经过滤的数据发送到外部服务时，选择 Mem0。

## 启用和检查 Memory

交互流程是最安全的入口：

```text
/memory
  -> Local memory
     -> On

/memory
  -> Cloud memory · Mem0
     -> On
```

等价的显式命令是：

```text
/memory local on
/memory local off
/memory mem0 on
/memory mem0 off
```

启用或禁用任一后端都是状态变更；存在活动的前台 Operation 时会被拒绝。选择会写入用户
配置，并在当前会话中生效。

本地开关把配置值、内存中的 service 标志和四项 Memory 工具注册视为一次状态转换。Core
会在写入配置前，依据 Registry 总计 128 项工具和 1 MiB catalog 上限校验完整候选集合。
若集合无法容纳，命令会返回 `tool_registry_limit`，而配置、service 状态和现有 Registry
均保持不变；系统不会暴露不完整的 Memory 工具集合。

使用以下命令检查和修改本地条目：

```text
/memory list user
/memory list workspace
/memory add user Prefer concise explanations with runnable examples.
/memory replace workspace memory_<32-hex-digits> Run unit tests before packaging.
/memory remove user memory_<32-hex-digits>
```

使用以下命令搜索和删除云端条目：

```text
/memory mem0 search preferred test command
/memory mem0 remove <mem0-memory-id>
```

精确语法见[命令参考](../reference/commands.zh-CN.md)。

## 配置

用户配置控制两种后端：

```yaml
version: 1
memory:
  local_file_memory: true
  mem0_cloud: false
```

用户 schema 中也存在 `mem0_user_id`，但 Awesome 会在首次启用 Mem0 时创建并管理这个不透明的
`user_<32-hex-digits>` 值。不要把其他用户的标识符复制进该文件。

Mem0 需要通过 `/auth mem0` 选中的凭据。环境值与由 Awesome 管理的值是彼此独立的来源；
修改或删除当前选中的来源，绝不会静默回退到另一个来源。秘密存放位置和完整选择规则见
[配置参考](../reference/configuration.zh-CN.md)。

与 DeepSeek 和 Kimi 不同，`/auth mem0` 不会发出远程验证请求。它只验证本地输入/存储的
形状并保存该值；只有当 Mem0 初始化、recall、删除或提炼实际联系服务时，身份验证失败才会
出现。因此，已保存 Mem0 key 并不能证明该凭据有效。

## 本地文件格式

本地 Memory 使用两个文件：

```text
<AWESOME_HOME>/memory/USER.md
<AWESOME_HOME>/workspaces/<workspace_key>/MEMORY.md
```

Awesome 只拥有被标记的区域，并保留它前后的 Markdown。生成的区域具有以下形状：

```markdown
# Notes I maintain myself

<!-- awesome-agent:managed-memory:start -->
<!-- memory:id=memory_0123456789abcdef0123456789abcdef -->
- Prefer focused diffs and explain any unverified risk.
<!-- awesome-agent:managed-memory:end -->
```

文件必须使用 UTF-8、最多 1,000,000 字节、包含至多一对顺序正确的标记，并且生成的条目 ID
必须唯一。无效的托管语法会使该作用域不可用；Awesome 不会猜测如何修复它。

每次修改都使用 compare-and-swap：

```text
read exact bytes -> SHA-256 content_hash -> validate proposed entry
                 -> re-read hash -> atomic sibling replace
```

该哈希防止命令或 Agent 工具静默覆盖并发的手动编辑。发生不匹配时会返回
`memory_conflict`；请重新列出文档，并基于新状态重复预期的修改。

只有启用本地 Memory 时，本地 Memory 工具才会出现：`memory_list`、`memory_add`、
`memory_replace` 和 `memory_remove`。面向模型的说明规定，只有当前用户明确请求时，
Agent 驱动的写入才有效。这是一条面向模型的指令，而不是运行时语义分类器。运行时，修改
要求匹配的受信任 Workspace、Agent 来源和活动 Turn，随后还要通过内容策略与
compare-and-swap 检查。它们的 `memory.read` 和 `memory.write` capability 不会通过三种
Workspace 权限模式弹出询问。

## 进入模型的内容

准备 Turn 时，Awesome 会为两份本地文档生成快照。本地条目按 `user`、再按 `workspace`
的顺序去重。如果启用了 Mem0，当前自然语言输入会成为搜索查询；与托管本地条目重复的云端
结果会被移除。托管标记区域之外手写的 Markdown 仍会进入本地上下文，但不属于 Mem0 去重
输入的一部分，因此等价的手写文本和云端文本可能同时出现。

本地与云端失败的处理刻意不同。启用本地 Memory 后，无效或不可读的 `USER.md` 或
`MEMORY.md` 会从快照捕获阶段向上传播，并使 Turn 准备失败。Awesome 不会静默地只使用
一个本地作用域继续，因为这会掩盖到底使用了哪些持久事实。Coordinator 会把已经创建的
Turn 终结为 failed，发出唯一的 Operation failed 结果并尝试删除其 checkpoint。清理失败
会被记录，但不会替换主要错误；启动 reconciliation 会重试删除 terminal Turn 遗留的
checkpoint。修复文档或禁用本地 Memory 后，后续 Turn 可以正常开始。相比之下，Mem0
搜索或初始化失败会变成有界诊断，Turn 会在没有云端来源的情况下继续。

```text
USER.md -----\
               -> normalize + deduplicate -> untrusted context sources
MEMORY.md ----/                                |
                                                +-> Context Builder -> model
current input -> Mem0 search -> scoped results |
```

所有 Memory 都被标记为**不受信任的参考上下文**，并作为 user-role 上下文呈现，而不是强制
system policy。在全部长期 Memory 中，Context Builder 最多使用有效输入上限的 10% 与
16,384 tokens 两者中的较小值。在这个池中，各来源的名义份额和硬上限是：

| 来源 | 份额 | 硬上限 |
| --- | ---: | ---: |
| User 本地 Memory | 25% | 4,096 tokens |
| Workspace 本地 Memory | 50% | 8,192 tokens |
| Mem0 recall | 25% | 4,096 tokens |

这些是最大值，而不是预留量。空来源或重复来源不会消耗上下文。使用 `/context` 检查实际
manifest。

## Mem0 写入与隐私管线

成功给出最终回答后，并且只有在 Turn 预算仍有余量时，Awesome 才可能额外使用至多一次模型
调用，提取最多五个稳定事实。提炼器收到的是当前用户文本（最多 4,000 个字符）和最终回答
（最多 8,000 个字符）经过脱敏后的片段，而不是完整对话或原始工具 transcript。

候选项随后在上传前经过策略检查。策略拒绝类似秘密的数据、凭据和私有绝对路径、原始
代码/diff/工具输出、可执行指令、仓库标识符和短暂任务状态。接受的候选项会被规范化，并按
作用域感知的 SHA-256 fact hash 去重。调用 Mem0 时会禁用 inference。

云端记录包含：

- 不透明的 Awesome user ID；
- `app_id: awesome-agent`；
- `scope: user|workspace`；
- fact hash；
- 仅 Workspace 作用域事实才有的不透明 workspace key。

Recall 最多返回八条记录。每项 Mem0 操作的期限是三秒。身份验证、速率限制、超时、响应
格式错误和服务错误会变成有界诊断码；它们不会把云端内容变成本地 Memory，也不会隐式重试
写入。

## 信任与取舍

- 本地文件是可读取的用户数据。用常规操作系统账户权限保护 `<AWESOME_HOME>`，不要在
  Memory 中存放秘密。
- Mem0 会把经过滤的事实和搜索查询发送给第三方。启用前请审查其数据政策。
- Memory 内容仍可能错误或具有对抗性。标签和 role placement 可以降低其权威性，但不能
  使内容变为事实。
- 提交到仓库的 `AGENTS.md` 或项目文档更适合强制团队规则。[Skill](skills.zh-CN.md) 更适合
  可复用流程。

如果 Memory 意外影响了回答，请依次运行 `/memory list user`、`/memory list workspace`、
`/memory mem0 search <query>` 和 `/context`。编辑或删除条目前，先禁用可疑后端。
