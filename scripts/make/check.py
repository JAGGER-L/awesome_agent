from __future__ import annotations

import shutil
import sys


def require(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise SystemExit(f"{name} was not found.")
    print(f"check.{name}=ok")
    return path


def main() -> None:
    require("uv")
    require("git")
    if sys.version_info[:2] != (3, 12):
        raise SystemExit(
            f"Python 3.12 is required, found "
            f"{sys.version_info.major}.{sys.version_info.minor}."
        )
    print("check.python_version=3.12")
    print("check.external_services=not_required")
    print("check.status=completed")


if __name__ == "__main__":
    main()
