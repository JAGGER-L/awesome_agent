# Installation

This page is for end users installing or upgrading a released build. It
explains the supported hosts, what the installer changes, how to verify the
result, and how to recover from an interrupted installation.

## Supported Hosts

The release installers intentionally support a narrow, tested matrix:

| Host | Supported architecture |
| --- | --- |
| Windows 11 | x64 |
| macOS | Apple Silicon (`arm64`) |
| WSL2 Ubuntu | Ubuntu 24.04 x64 |

Other Linux distributions, Intel macOS, Windows 10, and native Windows on Arm
are not part of the current release contract. This is a packaging decision,
not a claim that the Python source cannot run elsewhere: the installer bundles
and verifies exact runtime artifacts for the hosts above.

## Install a Release

The one-line commands below are the shortest path, but they execute a bootstrap
script directly from the network. Use the inspect-first workflow in
[Review the bootstrap before execution](#review-the-bootstrap-before-execution)
when repository policy requires local review or when you have not independently
established trust in the release source.

### Apple Silicon macOS or WSL2 Ubuntu 24.04 x64

Run the installer from an interactive POSIX shell:

```bash
curl -fsSL https://github.com/JAGGER-L/awesome_agent/releases/latest/download/install.sh | sh
```

The script requires `curl`. It installs the application under
`~/.local/share/awesome` and creates the public launcher at
`~/.local/bin/awesome`. It may add that launcher directory to the appropriate
shell profile. Open a new terminal after installation so the updated `PATH`
takes effect.

### Windows 11 x64

Run the installer from PowerShell:

```powershell
irm https://github.com/JAGGER-L/awesome_agent/releases/latest/download/install.ps1 | iex
```

The script installs the application under
`%LOCALAPPDATA%\Programs\Awesome` and adds its `bin` directory to the user
`PATH`. Open a new terminal after installation.

## Review the Bootstrap Before Execution

On macOS or WSL2, download the script to a temporary file, read it, and only
then execute the exact file you reviewed:

```bash
awesome_installer="$(mktemp)"
curl -fL https://github.com/JAGGER-L/awesome_agent/releases/latest/download/install.sh \
  -o "$awesome_installer"
less "$awesome_installer"
```

Only if you accept the reviewed script, execute and then remove it:

```bash
sh "$awesome_installer"
rm -f -- "$awesome_installer"
```

On Windows, use a process-specific temporary path:

```powershell
$AwesomeInstaller = Join-Path ([IO.Path]::GetTempPath()) "awesome-install-$PID.ps1"
Invoke-WebRequest `
  https://github.com/JAGGER-L/awesome_agent/releases/latest/download/install.ps1 `
  -OutFile $AwesomeInstaller
Get-Content -LiteralPath $AwesomeInstaller
```

Only if you accept the reviewed script, execute and then remove it:

```powershell
& $AwesomeInstaller
Remove-Item -LiteralPath $AwesomeInstaller
```

Reading a script is not cryptographic authentication. In both workflows you
still trust the repository/release account, HTTPS path, certificate handling,
and the code you execute. The bootstrap's SHA-256 checks protect the runtime and
application payloads it subsequently downloads; those checks do not
self-authenticate the already-running bootstrap script. Organizations that
need stronger provenance should mirror and approve both the bootstrap and its
pinned payloads before execution.

## What the Installer Does

The installer stages a complete candidate before it replaces the installed
application:

```text
download pinned bootstrap tools and release files
                     |
                     v
             verify SHA-256 checksums
                     |
                     v
 install private Python 3.12 + Node.js 22 runtimes
                     |
                     v
       install locked Core and TUI dependencies
                     |
                     v
       verify Core, Node, and public CLI versions
                     |
                     v
          replace the installed application
```

This design keeps product dependencies separate from system Python and Node.js
and avoids exposing a half-built application when validation fails. It does
not install Git. Install Git separately from the
[official Git site](https://git-scm.com/downloads) if your workflow needs it.

The installer downloads executable artifacts from GitHub, Astral, and
nodejs.org. In managed networks, those hosts must be reachable through the
organization's approved proxy and certificate policy.

## Verify the Installation

In a new terminal, run:

```text
awesome --version
awesome --help
```

The first command should print a numeric product version. The second should
show only the documented launch forms. Then enter a trusted project and run:

```text
cd <project>
awesome
```

Seeing the workspace trust prompt proves that the launcher, TUI, private Core,
and protocol handshake all started successfully. Continue with the
[Quickstart](quickstart.md); do not trust a path merely to test the prompt.

## Upgrade or Repair

Run the same one-line installer again. Reinstallation stages and validates the
new application before replacing the previous application files. User state,
credentials, configuration, Skills, and Memory live under `AWESOME_HOME`, not
inside the replaceable application directory.

Close all running Awesome sessions before upgrading. An open process may hold
application files or state leases, especially on Windows.

If startup reports that local state was created by a newer product version,
upgrade instead of resetting the data. If it offers an explicit state reset,
read [State and startup recovery](../concepts/changes-and-recovery.md) before
accepting.

## Uninstall or Remove Local Data

Awesome does not currently ship an automatic uninstaller. Close every Awesome
session first, then treat the application and user data as separate targets.
Removing the application does not require deleting conversation history or
credentials.

For a default release installation:

| Host | Remove the application | Remove the launcher/PATH entry |
| --- | --- | --- |
| Apple Silicon macOS or WSL2 | `~/.local/share/awesome` | Remove `~/.local/bin/awesome`; remove the installer-added `~/.local/bin` profile line only if no other program needs it. |
| Windows 11 | `%LOCALAPPDATA%\Programs\Awesome` | Remove that directory's `bin` entry from the user `PATH`. |

Before deleting, expand and inspect the exact absolute target. Do not delete a
broad home, profile, or `LOCALAPPDATA` directory. If the installation used a
non-default layout, identify the actual launcher and install directory rather
than assuming the table applies.

Keep the user-data root by default. It is `%LOCALAPPDATA%\Awesome` on Windows
and `~/.awesome` on macOS/WSL2 unless `AWESOME_HOME` was set before launch.
Deleting the complete, stopped user-data root permanently removes local
configuration, Awesome-managed Provider keys, UI preferences, User Skills,
Local Memory, conversations, trust, checkpoints, and Change Journal/undo data.
Back it up first using [Files and state](../reference/files-and-state.md), and
resolve an `AWESOME_HOME` override to one exact absolute directory before any
deletion. Never infer or recursively remove an empty or unresolved override.

Local deletion does not revoke Provider keys and does not erase records already
stored by an external service such as Mem0 Cloud. Revoke keys and delete remote
data in the relevant Provider console when that is part of the intended
offboarding.

## Installation Troubleshooting

### `awesome` is not found

Open a new terminal. Confirm `~/.local/bin` is on `PATH` on macOS/WSL2, or
`%LOCALAPPDATA%\Programs\Awesome\bin` is on the user `PATH` on Windows. Then
rerun the installer with every Awesome process closed.

### The host is rejected

Compare the host with the supported matrix above. The installer fails closed
instead of choosing an untested runtime archive. Contributors who intentionally
work outside the release matrix can review the
[development guide](../development/README.md), but that does not make the host
a supported release platform.

### A checksum or download fails

Do not bypass checksum validation. Retry after confirming network access and
the system clock. Persistent failures may indicate a proxy rewriting downloads
or an incomplete release; capture the exact installer error and consult
[Troubleshooting](../user-guide/troubleshooting.md).

## Next Step

Complete the [five-step Quickstart](quickstart.md), then read
[Permissions and safety](../user-guide/permissions.md) before allowing writes
or shell commands.
