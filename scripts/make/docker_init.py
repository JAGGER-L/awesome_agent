from __future__ import annotations

import subprocess


def main() -> None:
    subprocess.run(["docker", "compose", "build", "api", "worker"], check=True)
    subprocess.run(
        ["uv", "run", "python", "scripts/make/setup_sandbox.py"],
        check=True,
    )
    print("docker-init.status=completed")


if __name__ == "__main__":
    main()
