from __future__ import annotations

import subprocess
import time
import urllib.error
import urllib.request
from http.client import RemoteDisconnected
from pathlib import Path

from awesome_agent.cli.config_flow import initialize_user_config
from awesome_agent.paths import awesome_paths

ROOT = Path(__file__).resolve().parents[2]
API_URL = "http://127.0.0.1:8000"


def main() -> None:
    _ensure_awesome_home()
    if not _compose_declares_sandbox():
        raise SystemExit(
            "Docker Compose sandbox service is not present yet. Execute Task 63 "
            "before using make docker-start."
        )
    subprocess.run(["docker", "compose", "up", "-d", "postgres", "sandbox"], check=True)
    subprocess.run(
        ["docker", "compose", "run", "--rm", "api", "alembic", "upgrade", "head"],
        check=True,
    )
    subprocess.run(["docker", "compose", "up", "-d", "api", "worker"], check=True)
    _wait_for(f"{API_URL}/health")
    _wait_for(f"{API_URL}/ready?profile=api")
    print(f"docker-start.api={API_URL}")
    print(f"docker-start.docs={API_URL}/docs")
    print(
        "docker-start.note=Docker mode does not start the CLI. "
        "Use awesome locally for CLI/TUI."
    )
    print("docker-start.status=completed")


def _ensure_awesome_home() -> None:
    paths = awesome_paths()
    env_exists = paths.env_file.exists()
    initialize_user_config(paths)
    status = "exists" if env_exists else "created"
    print(f"docker-start.config={status} awesome_env {paths.env_file}")


def _compose_declares_sandbox() -> bool:
    compose = ROOT / "docker-compose.yml"
    text = compose.read_text(encoding="utf-8")
    return "\n  sandbox:" in text


def _wait_for(url: str, *, timeout_seconds: float = 60.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status < 500:
                    print(f"docker-start.ready={url}")
                    return
        except (RemoteDisconnected, urllib.error.URLError, TimeoutError):
            time.sleep(1)
    raise SystemExit(f"Timed out waiting for {url}")


if __name__ == "__main__":
    main()
