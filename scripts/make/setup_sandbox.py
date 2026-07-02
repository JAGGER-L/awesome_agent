from __future__ import annotations

import subprocess
from pathlib import Path


def main() -> None:
    dockerfile = Path("sandbox/aio/Dockerfile")
    if not dockerfile.exists():
        raise SystemExit(
            "AIO sandbox Dockerfile is not present yet. Execute Task 62 before "
            "using make setup-sandbox."
        )
    subprocess.run(
        [
            "docker",
            "build",
            "-f",
            str(dockerfile),
            "-t",
            "awesome-agent-sandbox:aio",
            ".",
        ],
        check=True,
    )
    print("setup-sandbox.backend=aio-docker")
    print("setup-sandbox.status=completed")


if __name__ == "__main__":
    main()
