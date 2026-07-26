# 参考

参考页面定义 Awesome 当前精确的公共契约。需要查找字段、默认值、限制、命令、路径或 wire
形状时，请使用这些页面。面向任务的指导从[用户指南](../user-guide/README.zh-CN.md)开始；
所有权和设计理由见[架构文档](../architecture/README.zh-CN.md)。

| 参考 | 规范事实 |
| --- | --- |
| [CLI 与键盘](cli.zh-CN.md) | 支持的启动 flag、终端要求、输入分类、编辑键和退出行为 |
| [Slash Commands](commands.zh-CN.md) | 完整命令 catalog、语法、所有权和前台准入规则 |
| [配置](configuration.zh-CN.md) | YAML 文档、所有字段和默认值、来源优先级、凭据与环境变量 |
| [内置工具](built-in-tools.zh-CN.md) | 工具名、capability、参数字段、限制、输出和条件支持工具 |
| [权限模式](permission-modes.zh-CN.md) | 精确的三模式矩阵、单次与 Thread grant、hard denial 和 Full access 确认 |
| [文件与状态](files-and-state.zh-CN.md) | User/workspace 路径、SQLite 所有权、schema 行为、锁、备份和重置边界 |
| [Protocol v3](protocol.zh-CN.md) | 私有 stdio JSON-RPC method、事件、错误、握手和 fixture |

## 如何阅读参考页面

表格描述当前源代码树中已经实现的行为。示例展示有效形状，但使用占位 ID、包名和凭据。
标记为**内部**的值用于解释状态或 wire 行为，不是受支持的用户编辑界面。

规范事实只在这里出现一次。叙述性页面链接到这里，而不复制字段列表和限制。当源代码改变
公共契约时，应在同一项变更中更新相应参考页面、聚焦的用户指南、相关 protocol fixture
以及测试。

## 版本边界

Awesome 有多个相互独立的版本：

- `VERSION` 和包 metadata 中的产品版本；
- 配置文档版本 `1`；
- Application SQLite schema 版本 `7`；
- 私有 Core/TUI protocol 版本 `3`；
- event envelope 版本 `1`；
- UI preferences schema 版本 `1`。

其中一个版本相同，并不意味着其他版本兼容。尤其是，私有 protocol 握手同时要求 protocol
v3 和完全一致的已安装产品版本，以便独立升级的 Core 与 TUI 组件能够明确失败。
