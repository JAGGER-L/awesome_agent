# Awesome

[English](README.md) | [简体中文](README.zh-CN.md)

```text
  ███  █   █ █████ █████  ███  █   █ █████
 █   █ █   █ █     █     █   █ ██ ██ █
 █████ █ █ █ ████  █████ █   █ █ █ █ ████
 █   █ ██ ██ █         █ █   █ █   █ █
 █   █ █   █ █████ █████  ███  █   █ █████
```

Awesome is an AI coding assistant that runs in your terminal. It can understand
your codebase, edit files, run commands, and help with development, debugging,
refactoring, and testing.

Start `awesome` in a project directory and describe your goal in natural
language. Awesome reads the relevant code, uses the tools it needs, makes the
change, and helps verify the result.

## What Awesome Can Do

- understand a project and explain how its code fits together;
- implement, debug, refactor, and test code;
- show controlled file changes with `/diff`, `/undo`, and `/redo`;
- search this Workspace's conversation history, then resume a result;
- fork a conversation at a terminal Turn, or retry that Turn in a fresh fork;
- export the current Thread as deterministic Markdown or JSON through the
  Change Journal;
- continue the latest Thread or resume one by ID;
- run one Agent Turn non-interactively with deterministic text or JSON output;
- choose Request approval, Accept edits, or Thread-scoped Full access;
- extend tasks with Skills, MCP tools, local Memory, and Mem0 Cloud;
- list, install, replace, and remove validated local User Skill packages without
  opening an interactive chat;
- search and extract public Web content through an optional, cited Tavily
  integration;
- work with DeepSeek and Kimi models.

Awesome starts with `ls`, `read_file`, `write_file`, `edit_file`, `delete`,
`glob`, `grep`, and `execute`. Extensions can add more tools; the total is not limited to eight.
Local memory and Mem0 Cloud are independent and default off.

Web tools also default off. Configure Tavily with masked `/auth tavily` or select
an existing `TAVILY_API_KEY`, enable Web with `/web on`, and approve the first
`network.read` request in each Thread. Search queries and
Fetch URLs are sent to Tavily under its [Privacy
Policy](https://www.tavily.com/privacy) and [Platform
Terms](https://www.tavily.com/terms); Awesome assigns stable `S1...` sources
and carries them into the final answer. Use `/web off` or `/web revoke` to
disable the integration or clear the active Thread grant.

## Install

### Apple Silicon macOS or WSL2 Ubuntu 24.04 x64

```bash
curl -fsSL https://github.com/JAGGER-L/awesome_agent/releases/latest/download/install.sh | sh
```

### Windows 11 x64

```powershell
irm https://github.com/JAGGER-L/awesome_agent/releases/latest/download/install.ps1 | iex
```

Open a new terminal after installation. Awesome includes the runtimes it needs;
you do not need to install Python, Node.js, uv, or npm first. Git is optional and
is never installed by Awesome. Install it from the
[official Git site](https://git-scm.com/downloads) when you want Git-aware
workflows.

## Start Awesome

Start Awesome inside a project:

```text
cd <project>
awesome
```

The first time Awesome opens a directory, it shows the path and asks whether
you trust it. Choose Yes only for projects you are comfortable allowing Awesome
to read and work in. Awesome starts in Request approval mode; use
`/permissions` to review or change the active Thread's mode.

After trust, a plain root `AGENTS.md` is read once as the session's project
instructions. Unsafe, non-UTF-8, binary, changing, or oversized files are
ignored as a whole and remain visible as a warning in Welcome, the status line,
and `/doctor`.

If no model Provider is configured, press Enter or run `/model`. Choose
DeepSeek or Kimi, paste the API key into the masked input, then select a model.
Use `/auth` later to add, replace, or remove credentials.

Useful launch options:

```text
awesome --continue
awesome --resume
awesome --resume <thread_id>
awesome run "Analyze the test failure" --trust-workspace
awesome skills list
awesome skills install ./review-api
awesome skills remove review-api --yes
awesome --version
awesome --help
```

`awesome run "<prompt>"` is the non-interactive path for scripts. It creates a
new Thread by default, writes only the final answer to stdout (`--format text`
or `--format json`), and sends diagnostics to stderr. Use `--thread <id>` to
target an existing Thread. Trust, permission checks, cancellation, and the same
private Core/Application lifecycle still apply; an unresolved interaction
exits without printing a partial answer. See the [CLI reference](docs/reference/cli.md)
for options and exit codes.

`awesome skills` is the standalone, non-chat package-management path for User
Skills. Install from a local package directory or ZIP, add `--replace` to
replace an existing package, and use `remove --yes` for unattended removal.
Without `--yes`, removal asks for confirmation only in an interactive terminal.
The official package command is one-shot and closes its private Core. An
already initialized Awesome Session keeps its immutable Skill catalog, so
restart that Session before selecting or loading a changed package. See
[Skills](docs/extensions/skills.md) for the package and safety contract.

If startup finds an unfinished Turn, Awesome asks before continuing it. A
verified local checkpoint offers Retry first only when the pending tool is safe
to repeat. An uncertain file mutation, shell command, MCP call, or Web request
offers Abort first and is never replayed automatically.

`/fork [turn_id]` materializes conversation history through a terminal Turn in
a new Thread. `/retry [turn_id]` materializes the prefix before that Turn, then
runs its original request again with the Turn's frozen model, Thinking, Skill,
and budgets. Both commands create independent records rather than a shared
history graph. They do not copy checkpoints, Tool activity, or ChangeSets;
retry does not replay old tool calls or undo their side effects.

## First Task

Try a read-only introduction to the project:

```text
Analyze this project's structure and tell me where I should start reading.
```

## Documentation

- [Browse the documentation site](https://jagger-l.github.io/awesome_agent/)
- [Documentation map](docs/README.md)
- [Install and complete the quickstart](docs/getting-started/quickstart.md)
- [Build a daily workflow](docs/user-guide/README.md)
- [Understand permissions and safe changes](docs/user-guide/permissions.md)
- [Choose Memory, Skills, or MCP](docs/extensions/README.md)
- [Look up exact commands, configuration, tools, and protocol](docs/reference/README.md)
- [Architecture](ARCHITECTURE.md)
- [Contributing and development](docs/development/README.md)
- [Troubleshooting](docs/user-guide/troubleshooting.md)
- [Roadmap](docs/roadmap.md)

Contributors can run the current checkout with `uv run awesome-dev`; see
[Development setup](docs/development/setup.md) for the complete environment,
startup, and troubleshooting flow.

## Security

Report vulnerabilities privately through the
[security policy](https://github.com/JAGGER-L/awesome_agent/blob/main/SECURITY.md)
([简体中文](https://github.com/JAGGER-L/awesome_agent/blob/main/SECURITY.zh-CN.md));
do not disclose sensitive details in a public issue.

Only trust projects you understand. Review `/diff` before keeping changes, and
enter credentials only through Awesome's masked `/model` or `/auth` flow. Full
access is Thread-scoped, applies only to built-in local capabilities, and does
not disable hard safety denials. MCP and unknown extension capabilities still
ask every time. None of the permission modes provides an operating-system
sandbox, and the command circuit breaker is a defense against recognizable
accidents rather than a detector for arbitrary hostile obfuscation.
Controlled workspace file operations bind checked directory/file identities
and do not follow links or reparse points to external targets. Recursive
inventory rejects nested reparse directories, while mutations reject ambiguous
or hard-link aliases. Bounded process-tree cleanup limits orphaned children but
does not isolate host execution.
Process-environment variables and `<AWESOME_HOME>/.env` remain advanced
configuration options; never put credentials in project files.
Optional Web search sends its query to Tavily. `web_fetch` sends one public
HTTPS URL to Tavily, whose cloud service retrieves and extracts that page;
Awesome Core does not connect to the target site itself. Neither tool inherits
ambient proxy variables or records query, URL, or result text in structured
diagnostics. Use `AWESOME_WEB_PROXY_URL` only when an explicit proxy is
required. Web Fetch is not a browser: it has no Cookie, login, JavaScript, PDF,
binary, local-fetch, cache, or backend-fallback capability.
