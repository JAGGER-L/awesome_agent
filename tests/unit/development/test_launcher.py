from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from awesome_agent.development import launcher


def _repository(tmp_path: Path) -> Path:
    root = tmp_path / "awesome"
    (root / "tui" / "node_modules").mkdir(parents=True)
    (root / "tui" / "dist" / "cli").mkdir(parents=True)
    (root / "tui" / "dist" / "cli" / "index.js").write_text(
        "// fixture", encoding="utf-8"
    )
    (root / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    return root


def _core(root: Path) -> Path:
    directory = root / ".venv" / ("Scripts" if os.name == "nt" else "bin")
    directory.mkdir(parents=True)
    executable = directory / ("awesome-core.exe" if os.name == "nt" else "awesome-core")
    executable.write_text("fixture", encoding="utf-8")
    return executable


def test_repository_root_is_derived_from_source_file() -> None:
    root = launcher.repository_root()
    assert (root / "pyproject.toml").is_file()
    assert (root / "tui" / "package.json").is_file()
    assert ".awesome-dev/" in (root / ".gitignore").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "relative",
    (
        Path(".venv/Scripts/awesome-core.exe"),
        Path(".venv/Scripts/awesome-core.cmd"),
        Path(".venv/bin/awesome-core"),
    ),
)
def test_core_discovery_supports_windows_macos_and_wsl_paths(
    tmp_path: Path, relative: Path
) -> None:
    root = _repository(tmp_path)
    candidate = root / relative
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_text("fixture", encoding="utf-8")

    assert launcher._core_executable(root) == candidate


def test_missing_workspace_fails_without_creating_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = _repository(tmp_path)
    missing = tmp_path / "missing-workspace"
    monkeypatch.setattr(launcher, "repository_root", lambda: root)

    assert launcher.main(["--workspace", str(missing)]) == 2
    assert "Workspace directory does not exist" in capsys.readouterr().err
    assert not missing.exists()


def test_default_paths_stay_under_ignored_development_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repository(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    core = _core(root)
    calls: list[tuple[list[str], Path, str, str]] = []

    monkeypatch.setattr(launcher, "repository_root", lambda: root)
    monkeypatch.setattr(launcher, "find_executable", lambda name: f"C:/{name}.exe")

    def run(argv: list[str], **options: object) -> subprocess.CompletedProcess[str]:
        cwd = options["cwd"]
        env = options["env"]
        assert isinstance(cwd, Path)
        assert isinstance(env, dict)
        calls.append((argv, cwd, str(env.get("AWESOME_HOME", "")), str(env["PATH"])))
        if argv[-1] == "--version":
            return subprocess.CompletedProcess(argv, 0, stdout="v22.18.0\n")
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(launcher, "run_process", run)

    assert launcher.main(["--workspace", str(workspace)]) == 0
    assert (root / ".awesome-dev" / "home").is_dir()
    assert (root / ".awesome-dev" / "logs").is_dir()
    assert calls[-1][1] == workspace.resolve()
    assert calls[-1][2] == str(root / ".awesome-dev" / "home")
    assert str(core.parent) == calls[-1][3].split(os.pathsep)[0]
    assert ".codex" not in calls[-1][2]
    assert not (workspace / ".awesome-dev").exists()


def test_explicit_home_is_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repository(tmp_path)
    _core(root)
    home = tmp_path / "chosen-home"
    observed: list[dict[str, str]] = []
    monkeypatch.setattr(launcher, "repository_root", lambda: root)
    monkeypatch.setattr(launcher, "find_executable", lambda name: name)
    monkeypatch.setenv("AWESOME_HOME", str(home))

    def run(argv: list[str], **options: object) -> subprocess.CompletedProcess[str]:
        if argv[-1] == "--version":
            return subprocess.CompletedProcess(argv, 0, stdout="v22.0.0")
        env = options["env"]
        assert isinstance(env, dict)
        observed.append({"AWESOME_HOME": str(env.get("AWESOME_HOME", ""))})
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(launcher, "run_process", run)
    assert launcher.main([]) == 0
    assert observed[-1]["AWESOME_HOME"] == str(home)


@pytest.mark.parametrize(
    ("missing", "message"),
    [("node", "Install Node.js 22"), ("npm", "Install Node.js 22")],
)
def test_missing_node_or_npm_is_actionable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    missing: str,
    message: str,
) -> None:
    root = _repository(tmp_path)
    _core(root)
    monkeypatch.setattr(launcher, "repository_root", lambda: root)
    monkeypatch.setattr(
        launcher,
        "find_executable",
        lambda name: None if name == missing else name,
    )
    assert launcher.main([]) == 2
    assert message in capsys.readouterr().err


def test_unsupported_node_version_is_actionable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = _repository(tmp_path)
    _core(root)
    monkeypatch.setattr(launcher, "repository_root", lambda: root)
    monkeypatch.setattr(launcher, "find_executable", lambda name: name)
    monkeypatch.setattr(
        launcher,
        "run_process",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, stdout="v20.19.0"
        ),
    )
    assert launcher.main([]) == 2
    assert "Node.js 22 or newer" in capsys.readouterr().err


def test_missing_dependencies_report_exact_recovery_commands(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = _repository(tmp_path)
    monkeypatch.setattr(launcher, "repository_root", lambda: root)
    monkeypatch.setattr(launcher, "find_executable", lambda name: name)
    monkeypatch.setattr(
        launcher,
        "run_process",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, stdout="v22.0.0"
        ),
    )

    assert launcher.main([]) == 2
    assert "uv sync --locked --extra memory" in capsys.readouterr().err

    _core(root)
    (root / "tui" / "node_modules").rmdir()
    assert launcher.main([]) == 2
    assert "npm ci --prefix tui" in capsys.readouterr().err


def test_build_failure_and_tui_exit_code_are_propagated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repository(tmp_path)
    _core(root)
    monkeypatch.setattr(launcher, "repository_root", lambda: root)
    monkeypatch.setattr(launcher, "find_executable", lambda name: name)
    results = iter(
        (
            subprocess.CompletedProcess(["node"], 0, stdout="v22.0.0"),
            subprocess.CompletedProcess(["npm"], 7),
        )
    )
    monkeypatch.setattr(launcher, "run_process", lambda *args, **kwargs: next(results))
    assert launcher.main([]) == 7

    results = iter(
        (
            subprocess.CompletedProcess(["node"], 0, stdout="v22.0.0"),
            subprocess.CompletedProcess(["npm"], 0),
            subprocess.CompletedProcess(["node"], 3),
        )
    )
    monkeypatch.setattr(launcher, "run_process", lambda *args, **kwargs: next(results))
    assert launcher.main([]) == 3


def test_keyboard_interrupt_returns_shell_interrupt_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repository(tmp_path)
    _core(root)
    monkeypatch.setattr(launcher, "repository_root", lambda: root)
    monkeypatch.setattr(launcher, "find_executable", lambda name: name)
    calls = 0

    def run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        del args, kwargs
        calls += 1
        if calls == 1:
            return subprocess.CompletedProcess(["node"], 0, stdout="v22.0.0")
        if calls == 2:
            return subprocess.CompletedProcess(["npm"], 0)
        raise KeyboardInterrupt

    monkeypatch.setattr(launcher, "run_process", run)
    assert launcher.main([]) == 130
