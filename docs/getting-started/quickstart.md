# Quickstart

Awesome is a local terminal coding agent. The only product surface is the Ink
`awesome` interface backed by the local Python `awesome-core` process. It does
not run an API server, PostgreSQL, Worker, or Docker service.

## Phase 4 Source Preview

The one-command end-user installer is delivered later in Phase 4. Until then,
this branch supports a contributor source preview only; it is not the final
installation experience.

Source-preview prerequisites are Git, Python 3.12 with uv, and Node.js 22 with
npm. These are development prerequisites only. Released users will not install
Python, Node, uv, or npm manually.

```powershell
uv sync --extra memory --dev
npm --prefix tui ci
npm --prefix tui run build
$env:PATH = "$(Resolve-Path .venv\Scripts);$env:PATH"
node tui/dist/cli/index.js --help
```

```bash
uv sync --extra memory --dev
npm --prefix tui ci
npm --prefix tui run build
export PATH="$PWD/.venv/bin:$PATH"
node tui/dist/cli/index.js --help
```

## Provider Credentials

Create `<AWESOME_HOME>/.env` and configure at least one supported Provider:

```dotenv
DEEPSEEK_API_KEY=
MOONSHOT_API_KEY=
```

The default user-data root is `%LOCALAPPDATA%\Awesome` on Windows and
`~/.awesome` on macOS/WSL2. Mem0 Cloud is optional and uses `MEM0_API_KEY`.

## Start In A Workspace

Run the compiled Ink entry from the directory you want Awesome to use as the
workspace. The first visit requires explicit trust. Trust denial exits without
running model or tool work.

```powershell
cd E:\path\to\project
node E:\path\to\awesome-agent\tui\dist\cli\index.js
```

The final V1 guide replaces this source-preview path with the one-command
installer and global `awesome` launcher.
