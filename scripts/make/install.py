from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _run(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    print(f"install.run={' '.join(args)}")
    return subprocess.run(args, cwd=ROOT, check=check, text=True)


def _require_uv() -> str:
    uv = shutil.which("uv")
    if uv is None:
        raise SystemExit(
            "uv is required before installing Awesome Agent. Install uv from "
            "https://docs.astral.sh/uv/getting-started/installation/, then open "
            "a new terminal and rerun make install."
        )
    return uv


def main() -> None:
    uv = _require_uv()
    _run([uv, "sync", "--dev", "--extra", "postgres", "--extra", "observability"])
    _run([uv, "tool", "install", "--editable", ".", "--force"])
    update_shell = _run([uv, "tool", "update-shell"], check=False)
    if update_shell.returncode != 0:
        print("install.path_update=manual")
        print("Run `uv tool update-shell`, then open a new terminal.")

    command = shutil.which("awesome")
    if command:
        _run(["awesome", "--help"], check=False)
    else:
        print("install.command_check=pending_new_shell")
    print("install.status=completed")
    print("Open a new terminal, then run:")
    print("  awesome --help")
    print("  awesome init")


if __name__ == "__main__":
    main()
