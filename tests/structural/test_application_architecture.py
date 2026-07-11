from __future__ import annotations

import ast
from pathlib import Path


def test_headless_acceptance_is_deterministic() -> None:
    path = Path("tests/integration/test_headless_product.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))

    assert not {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr in {"environ", "getenv"}
    }
