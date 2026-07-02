from __future__ import annotations

import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
API_URL = "http://127.0.0.1:8000"


def main() -> None:
    _ensure_env()
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


def _ensure_env() -> None:
    env_path = ROOT / ".env"
    if env_path.exists():
        print("dev.config=exists .env")
        return
    shutil.copyfile(ROOT / ".env.example", env_path)
    print("dev.config=created .env")


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
