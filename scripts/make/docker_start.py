from __future__ import annotations

import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
API_URL = "http://127.0.0.1:8000"


def main() -> None:
    _ensure_env()
    if not _compose_declares_sandbox():
        raise SystemExit(
            "Docker Compose sandbox service is not present yet. Execute Task 63 "
            "before using make docker-start."
        )
    subprocess.run(
        ["docker", "compose", "up", "-d", "postgres", "sandbox", "api", "worker"],
        check=True,
    )
    _wait_for(f"{API_URL}/health")
    _wait_for(f"{API_URL}/ready?profile=api")
    print(f"docker-start.api={API_URL}")
    print(f"docker-start.docs={API_URL}/docs")
    print(
        "docker-start.note=Docker mode does not start the CLI. "
        "Use awesome locally for CLI/TUI."
    )
    print("docker-start.status=completed")


def _ensure_env() -> None:
    env_path = ROOT / ".env"
    if env_path.exists():
        print("docker-start.config=exists .env")
        return
    shutil.copyfile(ROOT / ".env.example", env_path)
    print("docker-start.config=created .env")


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
        except (urllib.error.URLError, TimeoutError):
            time.sleep(1)
    raise SystemExit(f"Timed out waiting for {url}")


if __name__ == "__main__":
    main()
