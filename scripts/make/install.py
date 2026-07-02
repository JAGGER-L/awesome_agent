from __future__ import annotations

import subprocess


def main() -> None:
    subprocess.run(
        ["uv", "sync", "--dev", "--extra", "postgres", "--extra", "observability"],
        check=True,
    )
    print("install.status=completed")


if __name__ == "__main__":
    main()
