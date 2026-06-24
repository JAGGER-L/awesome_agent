import ast
from pathlib import Path


def test_domain_does_not_import_framework_or_infrastructure() -> None:
    forbidden_roots = {
        "fastapi",
        "langgraph",
        "openai",
        "sqlalchemy",
        "awesome_agent.persistence",
        "awesome_agent.providers",
        "awesome_agent.tools",
    }
    domain_root = Path("src/awesome_agent/domain")

    violations: list[str] = []
    for path in domain_root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if any(
                    name == root or name.startswith(f"{root}.")
                    for root in forbidden_roots
                ):
                    violations.append(f"{path}: {name}")

    assert not violations, "\n".join(violations)
