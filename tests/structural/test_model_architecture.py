from __future__ import annotations

import ast
from pathlib import Path

MODELING_MODULES = {
    "__init__.py",
    "catalog.py",
    "errors.py",
    "gateway.py",
    "messages.py",
    "provider.py",
    "stream.py",
    "tools.py",
    "turns.py",
}
PROVIDER_MODULES = {
    "__init__.py",
    "deepseek.py",
    "errors.py",
    "factory.py",
    "kimi.py",
}
SDK_PROVIDER_FILES = {"deepseek.py", "errors.py", "kimi.py"}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.add(node.module)
    return result


def test_model_and_provider_module_inventory_is_current() -> None:
    assert {
        path.name for path in Path("src/awesome_agent/modeling").glob("*.py")
    } == MODELING_MODULES
    assert {
        path.name for path in Path("src/awesome_agent/providers").glob("*.py")
    } == PROVIDER_MODULES


def test_modeling_contracts_are_provider_neutral() -> None:
    violations = {
        path.as_posix(): sorted(
            imported
            for imported in _imports(path)
            if imported == "openai"
            or imported.startswith("openai.")
            or imported == "awesome_agent.providers"
            or imported.startswith("awesome_agent.providers.")
        )
        for path in Path("src/awesome_agent/modeling").glob("*.py")
    }

    assert {path: imports for path, imports in violations.items() if imports} == {}


def test_provider_sdk_is_confined_to_concrete_adapters() -> None:
    root = Path("src/awesome_agent/providers")
    sdk_importers = {
        path.name
        for path in root.glob("*.py")
        if any(
            imported == "openai" or imported.startswith("openai.")
            for imported in _imports(path)
        )
    }

    assert sdk_importers <= SDK_PROVIDER_FILES


def test_adapters_use_official_base_urls() -> None:
    for path in (
        Path("src/awesome_agent/providers/deepseek.py"),
        Path("src/awesome_agent/providers/kimi.py"),
    ):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        constructors = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and node.name == "__init__"
        ]
        assert constructors
        assert all(
            "base_url"
            not in {
                argument.arg for argument in (*node.args.args, *node.args.kwonlyargs)
            }
            for node in constructors
        )
