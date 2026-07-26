# 安全策略

[English](SECURITY.md) | [简体中文](SECURITY.zh-CN.md)

Awesome 可以读取文件、修改受信工作区、启动本地进程并调用已配置的外部服务。当不受信输入
或损坏的边界能够导致超出用户授权的行为、泄露 secret 或私有数据、破坏持久状态、绕过强制
审批，或损害发布供应链时，该问题属于安全问题。

## 支持版本

安全修复面向最新发布的 GitHub Release 与当前开发分支。旧版本不作为独立安全维护线；用户
应升级到包含修复的最新 release。

## 私密报告

请使用 [GitHub 私密漏洞报告](https://github.com/JAGGER-L/awesome_agent/security/advisories/new)。
在协调披露前，不要创建公开 issue、discussion 或 pull request。报告应包含：

- 受影响的 release 或 commit，以及操作系统；
- 被违反的 trust、permission、protocol、filesystem、process、credential、storage、
  extension 或 supply-chain 边界；
- 只使用合成数据的最小复现；
- 可观察影响，以及操作是否越过 workspace 或 account 边界；
- 可能的缓解方案，但不得包含真实 secret 或私有数据。

维护者会在可用时间内确认并分类报告，与报告者协调验证和修复，并在受影响用户能够获得安全
版本后协商披露。不要测试你不拥有或未获授权的系统、账户、仓库或数据。

## 安全模型与限制

评估报告前，请阅读[安全与依赖边界](docs/architecture/security-and-dependencies.zh-CN.md)和
[权限机制](docs/user-guide/permissions.zh-CN.md)。权限提示与 command circuit breaker 用于降低
意外授权风险；它们不是操作系统 sandbox，也不能检测任意恶意混淆。Full access 不会自动
批准 MCP 或未来未知 capability。

非敏感缺陷请使用公开 bug-report 模板。绝不要在公开报告中附加 API key、`.env` 文件、私有
transcript、用户状态数据库或原始 Provider response。
