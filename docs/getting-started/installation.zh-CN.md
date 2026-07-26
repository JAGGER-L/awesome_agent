# 安装

本页面向安装或升级发布版的最终用户，说明受支持的主机、安装器会做出的更改、如何验证结果，以及如何从中断的安装中恢复。

## 受支持的主机

发布版安装器刻意采用较窄且经过测试的支持矩阵：

| 主机 | 支持的架构 |
| --- | --- |
| Windows 11 | x64 |
| macOS | Apple Silicon（`arm64`） |
| WSL2 Ubuntu | Ubuntu 24.04 x64 |

其他 Linux 发行版、Intel Mac、Windows 10 和 Arm 原生 Windows 不属于当前发布契约。这是打包决策，并不表示 Python 源码无法在其他环境运行：安装器只为上表中的主机打包并验证精确的运行时制品。

## 安装发布版

下面的单行命令是最短路径，但会直接执行来自网络的引导脚本。当仓库策略要求本地审查，或你尚未独立确认发布来源可信时，请改用[执行前审查引导脚本](#执行前审查引导脚本)中的流程。

### Apple Silicon macOS 或 WSL2 Ubuntu 24.04 x64

在交互式 POSIX shell 中运行安装器：

```bash
curl -fsSL https://github.com/JAGGER-L/awesome_agent/releases/latest/download/install.sh | sh
```

脚本需要 `curl`。它会把应用安装到 `~/.local/share/awesome`，并在 `~/.local/bin/awesome` 创建公开启动器。它可能会将启动器目录加入相应的 shell profile。安装完成后请打开新终端，让更新后的 `PATH` 生效。

### Windows 11 x64

在 PowerShell 中运行安装器：

```powershell
irm https://github.com/JAGGER-L/awesome_agent/releases/latest/download/install.ps1 | iex
```

脚本会把应用安装到 `%LOCALAPPDATA%\Programs\Awesome`，并将其中的 `bin` 目录添加到用户 `PATH`。安装完成后请打开新终端。

## 执行前审查引导脚本

在 macOS 或 WSL2 上，先把脚本下载到临时文件，阅读内容，然后只执行你审查过的那个文件：

```bash
awesome_installer="$(mktemp)"
curl -fL https://github.com/JAGGER-L/awesome_agent/releases/latest/download/install.sh \
  -o "$awesome_installer"
less "$awesome_installer"
```

只有在接受脚本内容后，才执行并删除它：

```bash
sh "$awesome_installer"
rm -f -- "$awesome_installer"
```

在 Windows 上，使用当前进程专属的临时路径：

```powershell
$AwesomeInstaller = Join-Path ([IO.Path]::GetTempPath()) "awesome-install-$PID.ps1"
Invoke-WebRequest `
  https://github.com/JAGGER-L/awesome_agent/releases/latest/download/install.ps1 `
  -OutFile $AwesomeInstaller
Get-Content -LiteralPath $AwesomeInstaller
```

只有在接受脚本内容后，才执行并删除它：

```powershell
& $AwesomeInstaller
Remove-Item -LiteralPath $AwesomeInstaller
```

阅读脚本不等于完成了密码学身份认证。在两种流程中，你仍然需要信任仓库/发布账户、HTTPS 路径、证书处理和将要执行的代码。引导脚本中的 SHA-256 校验会保护它随后下载的运行时和应用载荷；这些校验无法对已经在运行的引导脚本本身进行自认证。需要更强来源保证的组织，应在执行前镜像并审批引导脚本及其固定版本的载荷。

## 安装器会做什么

安装器会先暂存并完整验证候选版本，再替换已安装的应用：

```text
下载固定版本的引导工具和发布文件
                     |
                     v
              验证 SHA-256 校验和
                     |
                     v
 安装私有 Python 3.12 + Node.js 22 运行时
                     |
                     v
       安装锁定版本的 Core 与 TUI 依赖
                     |
                     v
       验证 Core、Node 和公开 CLI 版本
                     |
                     v
              替换已安装应用
```

该设计把产品依赖与系统 Python、Node.js 分离，并避免在验证失败时暴露一个只构建了一半的应用。它不会安装 Git；如工作流需要，请从 [Git 官方网站](https://git-scm.com/downloads)单独安装。

安装器会从 GitHub、Astral 和 nodejs.org 下载可执行制品。在受管理网络中，必须能通过组织批准的代理和证书策略访问这些主机。

## 验证安装

在新终端中运行：

```text
awesome --version
awesome --help
```

第一条命令应打印数字格式的产品版本；第二条命令应只显示文档记载的启动形式。然后进入一个受信项目并运行：

```text
cd <project>
awesome
```

看到 Workspace 信任提示，说明启动器、TUI、私有 Core 和协议握手都已成功启动。接下来完成[快速开始](quickstart.zh-CN.md)；不要仅为测试提示而信任一个路径。

## 升级或修复

再次运行同一个单行安装器即可。重新安装会先暂存并验证新应用，再替换原有应用文件。用户状态、凭据、配置、Skills 和 Memory 位于 `AWESOME_HOME` 下，而不在可替换的应用目录内。

升级前关闭所有正在运行的 Awesome 会话。活动进程可能持有应用文件或状态 lease，Windows 上尤其如此。

如果启动时报告本地状态由更新版本的产品创建，请升级产品而不是重置数据。如果界面明确提供状态重置选项，请在接受前阅读[状态与启动恢复](../concepts/changes-and-recovery.zh-CN.md)。

## 卸载或删除本地数据

Awesome 目前没有自动卸载程序。先关闭全部 Awesome 会话，再把应用和用户数据视为两个独立目标。删除应用不要求同时删除会话历史或凭据。

对于默认发布版安装：

| 主机 | 删除应用 | 删除启动器/PATH 条目 |
| --- | --- | --- |
| Apple Silicon macOS 或 WSL2 | `~/.local/share/awesome` | 删除 `~/.local/bin/awesome`；仅当其他程序不需要时，才删除安装器添加的 `~/.local/bin` profile 行。 |
| Windows 11 | `%LOCALAPPDATA%\Programs\Awesome` | 从用户 `PATH` 中删除该目录的 `bin` 条目。 |

删除前请展开并检查精确的绝对目标。不要删除范围宽泛的 home、profile 或 `LOCALAPPDATA` 目录。如果安装使用了非默认布局，请识别实际的启动器和安装目录，不要假定上表适用。

默认保留用户数据根目录。Windows 上是 `%LOCALAPPDATA%\Awesome`，macOS/WSL2 上是 `~/.awesome`，除非启动前设置了 `AWESOME_HOME`。删除已经停止使用的完整用户数据根目录，会永久移除本地配置、Awesome 管理的 Provider 密钥、UI 偏好、User Skills、Local Memory、会话、信任记录、checkpoint 和 Change Journal/undo 数据。请先按照[文件与状态](../reference/files-and-state.zh-CN.md)进行备份；若覆盖了 `AWESOME_HOME`，执行任何删除前都必须把它解析成一个精确的绝对目录。绝不能推断或递归删除空值或未解析的覆盖路径。

删除本地数据不会吊销 Provider 密钥，也不会清除 Mem0 Cloud 等外部服务已经存储的记录。如果离职/停用流程要求这样做，请在相应 Provider 控制台中吊销密钥并删除远端数据。

## 安装故障排查

### 找不到 `awesome`

打开新终端。macOS/WSL2 上确认 `~/.local/bin` 位于 `PATH`；Windows 上确认 `%LOCALAPPDATA%\Programs\Awesome\bin` 位于用户 `PATH`。然后关闭所有 Awesome 进程并重新运行安装器。

### 主机被拒绝

将当前主机与上面的支持矩阵对照。安装器会失败关闭，而不会选择未经测试的运行时归档。需要在发布矩阵之外开发的贡献者可以查阅[开发指南](../development/README.zh-CN.md)，但这不会使该主机成为受支持的发布平台。

### 校验和或下载失败

不要绕过校验和验证。确认网络访问和系统时钟无误后再重试。持续失败可能意味着代理改写了下载内容，或发布不完整；请记录安装器的精确错误并查阅[故障排查](../user-guide/troubleshooting.zh-CN.md)。

## 下一步

完成[五步快速开始](quickstart.zh-CN.md)，然后在允许写入或 shell 命令之前阅读[权限与安全](../user-guide/permissions.zh-CN.md)。
