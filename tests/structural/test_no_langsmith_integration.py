import ast
from pathlib import Path


def test_source_does_not_import_langsmith() -> None:
    violations: list[str] = []
    for path in Path("src").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            if any(
                module == "langsmith" or module.startswith("langsmith.")
                for module in modules
            ):
                violations.append(str(path))

    assert not violations, "LangSmith imports found:\n" + "\n".join(violations)
