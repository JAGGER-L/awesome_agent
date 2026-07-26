# 开始使用

本节面向第一次打开 Awesome 的用户。目标是让你从一个空终端开始，完成一次有实际价值且可审查的编码会话，而无需预先了解内部实现。

## Awesome 是什么

Awesome 是一款用于软件开发的本地终端应用。你用自然语言描述目标；Awesome 选择模型、组装项目上下文，并通过受控工具读取、编辑或运行代码。Ink 界面与 Python Core 一同发布，通过私有本地协议通信。你无需部署服务器，也无需一直开着浏览器标签页。

最简单且实用的心智模型是：

```text
你的请求
    |
    v
受信 Workspace -> 上下文 -> 模型 -> 工具提议 -> 策略/审批
                                                 |
                                                 v
                                          文件或 shell 操作
                                                 |
                                                 v
                                           结果 + 变更记录
```

这条流程很重要，因为模型的建议本身并不构成修改计算机的权限。Workspace 信任、权限模式、路径检查、命令安全和 Change Journal 是彼此独立的控制层。

## 选择阅读路径

- 如果尚未安装 Awesome，请从[安装](installation.zh-CN.md)开始。
- 如果已经安装并希望走最短的成功路径，请完成[五步快速开始](quickstart.zh-CN.md)。
- 如果你要为 Awesome 本身贡献代码，只有在也想把它用于日常工作时才需要安装发布版。源码开发流程请参阅[开发指南](../development/README.zh-CN.md)。

## 需要准备什么

你需要一台受支持的主机、一个你信任的项目目录，以及 DeepSeek 或 Kimi 的 API Key。Git 对大多数编码工作都很有用，但不是 Awesome 安装器的必需项。发布包包含私有的 Python 和 Node.js 运行时，因此无需另行安装这些运行时。

请只在 Provider 的官方控制台中创建密钥：

| Provider | 官方密钥页面 | 需要做出的选择 |
| --- | --- | --- |
| DeepSeek | [DeepSeek API Keys](https://platform.deepseek.com/api_keys) | 使用能够调用 API 的 DeepSeek 账户。 |
| Kimi，中国区 | [Kimi 中国区 API Keys](https://platform.kimi.com/console/api-keys) | 保持 `providers.kimi_region: cn`；请求使用中国区 API。 |
| Kimi，全球区 | [Kimi 全球区 API Keys](https://platform.kimi.ai/console/api-keys) | 设置 `providers.kimi_region: global`；请求使用全球区 API。 |

Kimi 账户和密钥可能有区域限制，因此所选区域必须与创建密钥的控制台一致。设置前请确认账户可用性、计费和网络访问。模型上下文会发送给所选的第三方 Provider；请查看该 Provider 当前适用于你所在组织的条款、隐私政策和数据控制。Awesome 的权限系统管理本地工具，而不决定 Provider 如何处理已提交的上下文。

Awesome 当前直接在宿主机上运行工具。权限提示和命令硬拒绝可以降低意外破坏的风险，但它们并不是操作系统沙箱。对于陌生或恶意仓库，请先使用外部虚拟机、容器或其他隔离边界，再授予信任。

## 推荐的第一次会话

先提出一个只读请求，例如：

```text
分析这个项目的结构，并告诉我应该从哪里开始阅读。
```

然后查看 `/context`、`/tools` 和 `/permissions`。在要求修改之前，这三个命令分别明确三件事：会发送哪些上下文、有哪些可用操作，以及哪些操作需要确认。

第一次编辑时，同时说明期望结果及验收方式：

```text
为空的显示名称增加验证。保持公共 API 不变，并在编辑后运行最小范围的相关测试。
```

继续操作或退出前，请检查生成的 `/diff`。

## 第一次会话之后

阅读[核心概念](../concepts/README.zh-CN.md)，了解 Workspace、Thread、Turn、Operation、上下文和恢复模型。日常工作请使用[用户指南](../user-guide/README.zh-CN.md)；需要精确的命令、字段、限制或工具参数时，可直接查阅完整的[参考手册](../reference/README.zh-CN.md)。
