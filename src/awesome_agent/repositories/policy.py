from __future__ import annotations

import os
from pathlib import Path


class RepositoryPathDenied(ValueError):
    pass


def normalize_path(path: Path) -> Path:
    return Path(os.path.normcase(str(path.expanduser().resolve())))


def ensure_allowed_path(path: Path, allowed_roots: list[Path]) -> Path:
    resolved = normalize_path(path)
    normalized_roots = [normalize_path(root) for root in allowed_roots]
    if not normalized_roots:
        raise RepositoryPathDenied("No repository roots are allowed.")
    if not any(
        resolved == root or resolved.is_relative_to(root) for root in normalized_roots
    ):
        raise RepositoryPathDenied(
            f"Repository path is outside configured allowed roots: {resolved}"
        )
    return resolved
