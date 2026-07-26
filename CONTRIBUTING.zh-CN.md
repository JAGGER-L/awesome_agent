# 为 Awesome 贡献

[English](CONTRIBUTING.md) | [简体中文](CONTRIBUTING.zh-CN.md)

感谢你改进 Awesome。只有用户行为、架构边界、测试、文档与发布影响彼此一致时，一项贡献
才算完整。仓库文件是事实来源；当当前代码与 issue 或聊天摘要不一致时，应以仓库为准。

## 从这里开始

1. 阅读[贡献者指南](docs/development/README.zh-CN.md)。
2. 按照[开发环境设置](docs/development/setup.zh-CN.md)准备环境。
3. 在[系统架构](ARCHITECTURE.zh-CN.md)中确认负责该行为的 package。
4. 从[测试与 CI](docs/development/testing.zh-CN.md)选择覆盖当前风险的最小验证集。
5. 修改协议、配置、存储、命令、工具或文档前，阅读[契约指南](docs/development/contracts-and-documentation.zh-CN.md)。

## 贡献契约

- Ink TUI 只负责呈现；Python Core 继续作为模型、工具、生命周期与持久化状态行为的权威。
- 保留无关工作，不进行机会主义重构。
- 修复缺陷时先加入或同时加入回归测试。不得用 skip、预期失败、放宽断言或兼容 shim
  隐藏漂移。
- 用户可见行为或架构变化必须同步更新英文与简体中文文档。
- 绝不提交 credential、私有路径、generated cache、debug payload 或复制的生产数据。
- 记录能够证明修改的命令与结果；未验证的平台、Provider 或发布证据必须明确说明。

Pull request 应只包含一个完整、连贯的改动。PR 模板会要求说明用户影响、架构理由、测试、
文档与剩余风险。安全漏洞应遵循[私密报告策略](SECURITY.zh-CN.md)，不要创建公开 issue。
