# Quickstart

[English](quickstart.md) | [简体中文](quickstart.zh-CN.md)

This guide gets you from a fresh checkout to one working Awesome Agent setup.
Choose one of three modes:

| Mode | Best for |
| --- | --- |
| Local CLI | Daily coding work inside a local project. |
| Local API | Running Awesome as a local API service. Currently Windows only. |
| Docker API | Running the API service through Docker. Currently Windows only. |

## Before You Start

Install the prerequisites:

- Python 3.12
- `uv`
- Git
- Docker Desktop on Windows, if you plan to use Local API or Docker API
- GNU Make, or an equivalent way to run the Makefile commands

Clone and install Awesome:

Windows PowerShell:

```powershell
git clone https://github.com/JAGGER-L/awesome_agent.git
cd awesome_agent
make install
```

macOS/Linux:

```bash
git clone https://github.com/JAGGER-L/awesome_agent.git
cd awesome_agent
make install
```

`make install` also installs the user-level `awesome` command with `uv tool`.
Open a new terminal after it finishes, then verify the command is on PATH:

Windows PowerShell:

```powershell
Get-Command awesome
awesome --help
```

macOS/Linux:

```bash
command -v awesome
awesome --help
```

If the command is still missing, run `uv tool update-shell`, open a new
terminal, and check again.

Create Awesome's user directory:

Windows PowerShell:

```powershell
awesome init
```

macOS/Linux:

```bash
awesome init
```

Add your model key. You can use an operating-system environment variable:

Windows PowerShell:

```powershell
setx AWESOME_AGENT_DEEPSEEK_API_KEY "your-key"
```

macOS/Linux:

```bash
export AWESOME_AGENT_DEEPSEEK_API_KEY="your-key"
```

Or add it to `<AWESOME_HOME>/.env`.

Windows PowerShell:

```powershell
$AwesomeHome = if ($env:AWESOME_HOME) { $env:AWESOME_HOME } else { Join-Path $env:LOCALAPPDATA "awesome-agent" }
New-Item -ItemType Directory -Force $AwesomeHome | Out-Null
Set-Content -Path (Join-Path $AwesomeHome ".env") -Value "AWESOME_AGENT_DEEPSEEK_API_KEY=your-key"
```

macOS/Linux:

```bash
mkdir -p "${AWESOME_HOME:-$HOME/.awesome-agent}"
printf 'AWESOME_AGENT_DEEPSEEK_API_KEY=your-key\n' > "${AWESOME_HOME:-$HOME/.awesome-agent}/.env"
```

Do not put this key in your project `.env`. Awesome reads provider keys from
the OS environment or from `<AWESOME_HOME>/.env`.

On Windows, `AWESOME_HOME` defaults to `%LOCALAPPDATA%\awesome-agent`. On other
platforms, it defaults to `~/.awesome-agent`.

## Choose A Mode

Use this table to choose the path to follow:

| Mode | Choose this if | Main command |
| --- | --- | --- |
| Local CLI | You want to chat with Awesome in a project folder. | `awesome` |
| Local API | You want a local API endpoint and browser API docs. Currently Windows only. | `make dev` |
| Docker API | You want the API service to run through Docker. Currently Windows only. | `make docker-start` |

## Option 1: Local CLI

### When To Use

Use Local CLI when you want Awesome to work directly inside a local project.
This is the simplest path and the recommended starting point.

### Configure

From the Awesome checkout:

Windows PowerShell:

```powershell
awesome init
awesome doctor
```

macOS/Linux:

```bash
awesome init
awesome doctor
```

If `awesome doctor` reports a missing API key, add
`AWESOME_AGENT_DEEPSEEK_API_KEY` to the OS environment or `<AWESOME_HOME>/.env`,
then restart your terminal.

### Start

Open the project you want Awesome to work on:

Windows PowerShell:

```powershell
cd E:\my-project
awesome
```

macOS/Linux:

```bash
cd ~/my-project
awesome
```

### Verify

Send a normal message:

```text
Read this project and explain how it is organized.
```

Success means the welcome screen does not show a missing-key error and Awesome
starts responding in the terminal.

### Stop

Use `/quit` inside Awesome, or press `Ctrl+C`.

## Option 2: Local API

### When To Use

Use Local API when you want Awesome available through a local API endpoint or
you want to inspect the generated API docs in a browser.

Local API is currently documented and supported for Windows only.

### Configure

From the Awesome checkout:

```powershell
awesome init
```

Make sure your API key is set in the OS environment or `<AWESOME_HOME>/.env`.

### Deploy

Prepare Local API support:

```powershell
make setup-sandbox
```

### Start

Start the local API mode:

```powershell
make dev
```

### Verify

Open:

```text
http://127.0.0.1:8000/docs
```

Success means the API docs page loads in your browser.

### Stop

Return to the terminal running `make dev` and press `Ctrl+C`.

## Option 3: Docker API

### When To Use

Use Docker API when you want the API service to run through Docker instead of
directly from your host Python environment.

Docker API is currently documented and supported for Windows only.

Docker API does not start Local CLI. Use `awesome` separately if you also want
the terminal chat interface.

### Configure

From the Awesome checkout:

```powershell
awesome init
```

Make sure Docker Desktop is running and your API key is set in the OS
environment or `<AWESOME_HOME>/.env`.

### Deploy

Prepare the Docker API mode:

```powershell
make docker-init
```

### Start

Start the Docker API mode:

```powershell
make docker-start
```

### Verify

Open:

```text
http://127.0.0.1:8000/docs
```

Success means the API docs page loads in your browser.

### Stop

From the Awesome checkout:

```powershell
docker compose down
```

## Common Problems

### API key is missing

Set `AWESOME_AGENT_DEEPSEEK_API_KEY` in your OS environment or
`<AWESOME_HOME>/.env`, then restart your terminal.

### `awesome` command not found

Go back to the Awesome checkout and run:

```powershell
make install
```

Then open a new terminal and try `awesome --help`.

### Awesome opened in the wrong project

Exit Awesome, change to the project directory you want to work in, and start it
again:

Windows PowerShell:

```powershell
cd E:\my-project
awesome
```

macOS/Linux:

```bash
cd ~/my-project
awesome
```

### Docker is not running

Start Docker Desktop, wait until it is ready, then run the Docker command again.

## Next Steps

- Use `/help` inside Awesome to see available commands.
- Use `/config` to confirm which Awesome paths are active.
- Add project skills under `<your-project>/skills/`.
- Add personal skills under `<AWESOME_HOME>/skills/`.
