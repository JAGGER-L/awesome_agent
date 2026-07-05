# 快速开始

[English](quickstart.md) | [简体中文](quickstart.zh-CN.md)

本指南帮助你从全新的源码目录开始，完成一个可用的 Awesome Agent 设置。你可以
选择三种方式之一：

| 方式 | 最适合 |
| --- | --- |
| Local CLI | 在本地项目目录中日常使用 Awesome。 |
| Local API | 在本机启动 Awesome API service。目前只支持 Windows。 |
| Docker API | 通过 Docker 运行 API service。目前只支持 Windows。 |

## 开始之前

先安装前置依赖：

- Python 3.12
- `uv`
- Git
- Docker Desktop，如果你要在 Windows 上使用 Local API 或 Docker API
- GNU Make，或其它可以运行 Makefile 命令的方式

克隆并安装 Awesome：

Windows PowerShell：

```powershell
git clone https://github.com/JAGGER-L/awesome_agent.git
cd awesome_agent
make install
```

macOS/Linux：

```bash
git clone https://github.com/JAGGER-L/awesome_agent.git
cd awesome_agent
make install
```

创建 Awesome 用户目录：

Windows PowerShell：

```powershell
awesome init
```

macOS/Linux：

```bash
awesome init
```

添加模型 key。你可以使用操作系统环境变量：

Windows PowerShell：

```powershell
setx AWESOME_AGENT_DEEPSEEK_API_KEY "your-key"
```

macOS/Linux：

```bash
export AWESOME_AGENT_DEEPSEEK_API_KEY="your-key"
```

也可以写入 `<AWESOME_HOME>/.env`。

Windows PowerShell：

```powershell
$AwesomeHome = if ($env:AWESOME_HOME) { $env:AWESOME_HOME } else { Join-Path $env:LOCALAPPDATA "awesome-agent" }
New-Item -ItemType Directory -Force $AwesomeHome | Out-Null
Set-Content -Path (Join-Path $AwesomeHome ".env") -Value "AWESOME_AGENT_DEEPSEEK_API_KEY=your-key"
```

macOS/Linux：

```bash
mkdir -p "${AWESOME_HOME:-$HOME/.awesome-agent}"
printf 'AWESOME_AGENT_DEEPSEEK_API_KEY=your-key\n' > "${AWESOME_HOME:-$HOME/.awesome-agent}/.env"
```

不要把这个 key 放进项目 `.env`。Awesome 只从操作系统环境变量或
`<AWESOME_HOME>/.env` 读取模型密钥。

Windows 上，`AWESOME_HOME` 默认是 `%LOCALAPPDATA%\awesome-agent`。其它平台
默认是 `~/.awesome-agent`。

## 选择一种方式

用下面的表格选择要走的路径：

| 方式 | 适合场景 | 主要命令 |
| --- | --- | --- |
| Local CLI | 你想在项目目录中直接和 Awesome 对话。 | `awesome` |
| Local API | 你想要本地 API 地址和浏览器接口文档。目前只支持 Windows。 | `make dev` |
| Docker API | 你想通过 Docker 运行 API service。目前只支持 Windows。 | `make docker-start` |

## 方式一：Local CLI

### 什么时候使用

当你希望 Awesome 直接在本地项目中工作时，使用 Local CLI。这是最简单的方式，
也是推荐的起点。

### 配置

在 Awesome checkout 中运行：

Windows PowerShell：

```powershell
awesome init
awesome doctor
```

macOS/Linux：

```bash
awesome init
awesome doctor
```

如果 `awesome doctor` 提示缺少 API key，请把
`AWESOME_AGENT_DEEPSEEK_API_KEY` 添加到操作系统环境变量或
`<AWESOME_HOME>/.env`，然后重启终端。

### 启动

打开你希望 Awesome 工作的项目：

Windows PowerShell：

```powershell
cd E:\my-project
awesome
```

macOS/Linux：

```bash
cd ~/my-project
awesome
```

### 验证

发送一条普通消息：

```text
Read this project and explain how it is organized.
```

如果欢迎界面不再提示缺少 API key，并且 Awesome 开始在终端中回复，就说明启动
成功。

### 停止

在 Awesome 中使用 `/quit`，或按 `Ctrl+C`。

## 方式二：Local API

### 什么时候使用

当你希望通过本地 API 地址使用 Awesome，或想在浏览器中查看接口文档时，
使用 Local API。

Local API 目前只提供 Windows 使用说明并按 Windows 支持。

### 配置

在 Awesome checkout 中运行：

```powershell
awesome init
```

确认 API key 已经设置在操作系统环境变量或 `<AWESOME_HOME>/.env` 中。

### 部署

准备 Local API 支持文件：

```powershell
make setup-sandbox
```

### 启动

启动 Local API：

```powershell
make dev
```

### 验证

打开：

```text
http://127.0.0.1:8000/docs
```

如果浏览器中能打开接口文档页面，就说明启动成功。

### 停止

回到运行 `make dev` 的终端，按 `Ctrl+C`。

## 方式三：Docker API

### 什么时候使用

当你希望 API service 通过 Docker 运行，而不是直接使用本机 Python 环境时，
使用 Docker API。

Docker API 目前只提供 Windows 使用说明并按 Windows 支持。

Docker API 不会启动 Local CLI。如果你还需要终端聊天界面，请单独运行
`awesome`。

### 配置

在 Awesome checkout 中运行：

```powershell
awesome init
```

确认 Docker Desktop 已启动，并且 API key 已经设置在操作系统环境变量或
`<AWESOME_HOME>/.env` 中。

### 部署

准备 Docker API：

```powershell
make docker-init
```

### 启动

启动 Docker API：

```powershell
make docker-start
```

### 验证

打开：

```text
http://127.0.0.1:8000/docs
```

如果浏览器中能打开接口文档页面，就说明启动成功。

### 停止

在 Awesome checkout 中运行：

```powershell
docker compose down
```

## 常见问题

### API key is missing

把 `AWESOME_AGENT_DEEPSEEK_API_KEY` 设置到操作系统环境变量或
`<AWESOME_HOME>/.env`，然后重启终端。

### 找不到 `awesome` 命令

回到 Awesome checkout，运行：

```powershell
make install
```

然后打开新的终端，尝试运行 `awesome --help`。

### API 备用命令

`awesome-agent start` 只作为 Local API 开发时的备用/调试 supervisor 使用。日常
项目工作应使用 `awesome`；API 模式通常应使用 `make dev` 或
`make docker-start`。

### Awesome 在错误的项目中启动

退出 Awesome，切换到你希望处理的项目目录，再重新启动：

Windows PowerShell：

```powershell
cd E:\my-project
awesome
```

macOS/Linux：

```bash
cd ~/my-project
awesome
```

### Docker 没有运行

启动 Docker Desktop，等它准备好后，再重新运行 Docker 命令。

## 下一步

- 在 Awesome 中使用 `/help` 查看可用命令。
- 使用 `/config` 确认当前使用的 Awesome 路径。
- 把项目级 skills 放到 `<your-project>/skills/`。
- 把个人 skills 放到 `<AWESOME_HOME>/skills/`。
