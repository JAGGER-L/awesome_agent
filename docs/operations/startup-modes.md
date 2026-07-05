# Startup Modes

## Local CLI

Use Local CLI for normal project work:

```powershell
cd E:\my-project
awesome
```

`awesome` starts the chat-first local TUI. It does not require a separately
running API server for ordinary local use.

## Local API

Use Local API when you want an API endpoint or browser API docs from the host
Python environment. This path is currently documented for Windows:

```powershell
make check
make install
make setup-sandbox
make dev
```

## Docker API

Use Docker API when you want the API service to run through Docker:

```powershell
make docker-init
make docker-start
```

Docker API starts API, Worker, PostgreSQL, and sandbox services. Docker mode
does not start the CLI; run `awesome` separately for the terminal chat
interface.

## Fallback And Debug Commands

`awesome-agent start` is a fallback/debug supervisor for API and Worker in one
local process group. Use `awesome-agent serve` and `awesome-agent worker`
separately when another process manager should own them.
