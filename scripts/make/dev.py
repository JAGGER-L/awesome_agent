from __future__ import annotations

import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from awesome_agent.cli.config_flow import initialize_user_config
from awesome_agent.paths import awesome_paths

ROOT = Path(__file__).resolve().parents[2]
API_URL = "http://127.0.0.1:8000"


def main() -> None:
    _ensure_awesome_home()
    subprocess.run(["docker", "compose", "up", "-d", "postgres"], check=True)
    subprocess.run(["uv", "run", "alembic", "upgrade", "head"], check=True)
    executable = _agent_executable()
    process = subprocess.Popen([str(executable), "start"], cwd=ROOT)
    try:
        _wait_for(f"{API_URL}/health")
        _wait_for(f"{API_URL}/ready?profile=api")
    except Exception:
        process.terminate()
        raise
    print(f"dev.api={API_URL}")
    print(f"dev.docs={API_URL}/docs")
    print("dev.status=completed")


def _ensure_awesome_home() -> None:
    paths = awesome_paths()
    env_exists = paths.env_file.exists()
    initialize_user_config(paths)
    status = "exists" if env_exists else "created"
    print(f"dev.config={status} awesome_env {paths.env_file}")


def _agent_executable() -> Path:
    if sys.platform == "win32":
        candidate = ROOT / ".venv" / "Scripts" / "awesome-agent.exe"
    else:
        candidate = ROOT / ".venv" / "bin" / "awesome-agent"
    if not candidate.exists():
        raise SystemExit("awesome-agent executable not found. Run make install first.")
    return candidate


def _wait_for(url: str, *, timeout_seconds: float = 60.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status < 500:
                    print(f"dev.ready={url}")
                    return
        except (urllib.error.URLError, TimeoutError):
            time.sleep(1)
    raise SystemExit(f"Timed out waiting for {url}")


if __name__ == "__main__":
    main()
