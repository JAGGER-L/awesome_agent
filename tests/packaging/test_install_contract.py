from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_shell_installer_has_safe_supported_host_contract() -> None:
    source = (ROOT / "install.sh").read_text(encoding="utf-8")

    assert source.startswith("#!/bin/sh\nset -eu\n")
    for required in {
        'VERSION="1.0.0"',
        'UV_VERSION="0.11.28"',
        'NODE_VERSION="22.23.1"',
        "releases/latest/download",
        "awesome-$VERSION.zip",
        "SHA256SUMS",
        'UV_DARWIN_SHA256="33540eb7c883ab857eff79bd5ac2aa31fe27b595abecb4a9c003a2c998447232"',
        'UV_LINUX_SHA256="e490a6464492183c5d4534a5527fb4440f7f2bb2f228162ad7e4afe076dc0224"',
        'NODE_DARWIN_SHA256="fb526811860f81dcac7dd8b2b55eca4accfc5d61c3b7c2508f2639faee8a738d"',
        'NODE_LINUX_SHA256="9749e988f437343b7fa832c69ded82a312e41a03116d766797ac14f6f9eee578"',
        "releases.astral.sh/github/uv/releases/download",
        "sha256_file",
        "UV_PYTHON_INSTALL_DIR",
        "--managed-python",
        "--no-bin",
        "os.path.realpath",
        "npm-cli.js",
        "--target",
        "requirements.lock",
        "--require-hashes",
        "--no-deps",
        "PYTHONPATH",
        "site.addsitedir",
        "ci --omit=dev --ignore-scripts",
        "uname -s",
        "uname -m",
        "Darwin",
        "arm64",
        "x86_64",
        "/proc/sys/kernel/osrelease",
        "/etc/os-release",
        "24.04",
        "mktemp -d",
        "trap cleanup EXIT",
        "curl",
        "git-scm.com/downloads",
        ".zprofile",
        ".profile",
    }:
        assert required in source
    assert source.index('echo "validated"') < source.index('rm -rf "$INSTALL_ROOT/app"')
    assert source.count('export PATH="$HOME/.local/bin:$PATH"') == 1
    assert "uv-install.sh" not in source


def test_shell_installer_parses_when_posix_sh_is_available() -> None:
    shell = shutil.which("sh")
    if shell is None:
        pytest.skip("POSIX sh is unavailable on this host")
    subprocess.run([shell, "-n", str(ROOT / "install.sh")], check=True)


def test_windows_installer_has_safe_supported_host_contract() -> None:
    source = (ROOT / "install.ps1").read_text(encoding="utf-8")

    for required in {
        '$Version = "1.0.0"',
        '$UvVersion = "0.11.28"',
        '$NodeVersion = "22.23.1"',
        "releases/latest/download",
        "Is64BitOperatingSystem",
        "PROCESSOR_ARCHITECTURE",
        "22000",
        "LOCALAPPDATA",
        '"Programs\\Awesome"',
        "Invoke-WebRequest",
        "Get-FileHash",
        "Expand-Archive",
        '$UvSha256 = "'
        "0a23463216d09c6a72ff80ef5dc5a795f07dc1575cb84d24596c2f124a441b7b"
        '"',
        '$NodeSha256 = "'
        "7df0bc9375723f4a86b3aa1b7cc73342423d9677a8df4538aca31a049e309c29"
        '"',
        "releases.astral.sh/github/uv/releases/download",
        "UV_PYTHON_INSTALL_DIR",
        "--no-registry",
        "--managed-python",
        "--no-bin",
        "os.path.realpath",
        "node-v$NodeVersion-win-x64.zip",
        "npm-cli.js",
        "--target",
        "requirements.lock",
        "--require-hashes",
        "--no-deps",
        "PYTHONPATH",
        "site.addsitedir",
        "--omit=dev",
        "--ignore-scripts",
        'Write-Output "validated"',
        "awesome.cmd",
        'SetEnvironmentVariable("Path", $UpdatedPath, "User")',
        "git-scm.com/downloads",
    }:
        assert required in source
    assert source.index("Is64BitOperatingSystem") < source.index("$Stage = Join-Path")
    assert source.index('Write-Output "validated"') < source.index(
        "Remove-Item -LiteralPath $InstalledApp"
    )
    assert "\"$LauncherDir;$($UserPath.TrimStart(';'))\"" in source
    assert source.count("Get-FileHash") >= 2
    assert source.count("Assert-FileSha256") >= 3
    assert "uv-install.ps1" not in source


def test_windows_installer_parses_in_powershell() -> None:
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if powershell is None:
        pytest.skip("PowerShell is unavailable on this host")
    command = (
        "$errors = $null; "
        "$tokens = $null; "
        "[System.Management.Automation.Language.Parser]::ParseFile("
        "(Resolve-Path 'install.ps1'), [ref]$tokens, [ref]$errors) | Out-Null; "
        "if ($errors.Count) { $errors | Out-String | Write-Error; exit 1 }"
    )
    subprocess.run(
        [powershell, "-NoProfile", "-Command", command], cwd=ROOT, check=True
    )
