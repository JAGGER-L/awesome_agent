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
        "UV_UNMANAGED_INSTALL",
        "UV_PYTHON_INSTALL_DIR",
        "npm-cli.js",
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
    lowered = source.casefold()
    for forbidden in {
        "wget",
        "sudo",
        "apt install git",
        "brew install git",
        "docker",
        "make install",
        "awesome_home",
        "versions/",
        "current/",
        "backup",
        "rollback",
        "uninstall",
    }:
        assert forbidden not in lowered
    assert source.index('echo "validated"') < source.index('rm -rf "$INSTALL_ROOT/app"')
    assert source.count('export PATH="$HOME/.local/bin:$PATH"') == 1


def test_shell_installer_parses_when_posix_sh_is_available() -> None:
    shell = shutil.which("sh")
    if shell is None:
        pytest.skip("POSIX sh is unavailable on this host")
    subprocess.run([shell, "-n", str(ROOT / "install.sh")], check=True)
