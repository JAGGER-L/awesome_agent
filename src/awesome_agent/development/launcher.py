from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

find_executable = shutil.which
run_process = subprocess.run


class DevelopmentLaunchError(RuntimeError):
    def __init__(self, message: str, *, exit_code: int = 2) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def repository_root() -> Path:
    root = Path(__file__).resolve().parents[3]
    if (
        not (root / "pyproject.toml").is_file()
        or not (root / "tui" / "package.json").is_file()
    ):
        raise DevelopmentLaunchError(
            "awesome-dev must run from an editable Awesome source checkout."
        )
    return root


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="awesome-dev",
        description="Build and run Awesome from the current source checkout.",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        help="Project directory to open. Defaults to the current directory.",
    )
    return parser


def _command(name: str) -> str:
    resolved = find_executable(name)
    if resolved is None:
        raise DevelopmentLaunchError(
            "Install Node.js 22 or newer, which includes npm, then retry."
        )
    return resolved


def _node_version(node: str, *, root: Path, environment: dict[str, str]) -> None:
    result = run_process(
        [node, "--version"],
        cwd=root,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    version = result.stdout.strip()
    match = re.fullmatch(r"v?(\d+)(?:\.\d+){0,2}", version)
    if result.returncode != 0 or match is None or int(match.group(1)) < 22:
        raise DevelopmentLaunchError(
            "awesome-dev requires Node.js 22 or newer. Install it and retry."
        )


def _core_executable(root: Path) -> Path:
    candidates = (
        root / ".venv" / "Scripts" / "awesome-core.exe",
        root / ".venv" / "Scripts" / "awesome-core.cmd",
        root / ".venv" / "bin" / "awesome-core",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise DevelopmentLaunchError(
        "The Awesome Python environment is not ready. Run "
        "`uv sync --locked --extra memory` and retry."
    )


def _validate_workspace(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_dir():
        raise DevelopmentLaunchError(f"Workspace directory does not exist: {resolved}")
    return resolved


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        root = repository_root()
        workspace = _validate_workspace(arguments.workspace or Path.cwd())
        environment = dict(os.environ)
        node = _command("node")
        npm = _command("npm")
        _node_version(node, root=root, environment=environment)
        core = _core_executable(root)
        if not (root / "tui" / "node_modules").is_dir():
            raise DevelopmentLaunchError(
                "The TUI dependencies are not installed. Run "
                "`npm ci --prefix tui` and retry."
            )

        development_root = root / ".awesome-dev"
        home = Path(environment.get("AWESOME_HOME", development_root / "home"))
        home = home.expanduser().resolve()
        home.mkdir(parents=True, exist_ok=True)
        (development_root / "logs").mkdir(parents=True, exist_ok=True)

        build = run_process(
            [npm, "--prefix", str(root / "tui"), "run", "build"],
            cwd=root,
            env=environment,
            check=False,
        )
        if build.returncode != 0:
            print(
                f"The TUI build failed with exit code {build.returncode}.",
                file=sys.stderr,
            )
            return build.returncode or 1

        entrypoint = root / "tui" / "dist" / "cli" / "index.js"
        if not entrypoint.is_file():
            raise DevelopmentLaunchError(
                "The TUI build completed without dist/cli/index.js."
            )
        child_environment = dict(environment)
        child_environment["AWESOME_HOME"] = str(home)
        child_environment["PATH"] = os.pathsep.join(
            (str(core.parent), environment.get("PATH", ""))
        ).rstrip(os.pathsep)
        try:
            child = run_process(
                [node, str(entrypoint)],
                cwd=workspace,
                env=child_environment,
                check=False,
            )
        except KeyboardInterrupt:
            return 130
        return child.returncode
    except DevelopmentLaunchError as error:
        print(str(error), file=sys.stderr)
        return error.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
