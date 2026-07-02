from __future__ import annotations

import subprocess


def main() -> None:
    subprocess.run(
        ["docker", "compose", "build", "sandbox", "api", "worker"],
        check=True,
    )
    print("docker-init.status=completed")


if __name__ == "__main__":
    main()
