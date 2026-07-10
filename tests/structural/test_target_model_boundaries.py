from __future__ import annotations

import ast
from pathlib import Path

ISOLATED_LEGACY_MODELING = {
    "execution.py",
    "execution_jsonl.py",
    "model_worker.py",
    "process_backend.py",
}
FORBIDDEN_MODELING_IMPORTS = {
    "awesome_agent.api",
    "awesome_agent.application",
    "awesome_agent.persistence",
    "awesome_agent.runtime",
    "awesome_agent.settings",
    "awesome_agent.storage",
    "fastapi",
    "langgraph",
    "openai",
    "sqlalchemy",
}
FORBIDDEN_PROVIDER_IMPORTS = {
    "awesome_agent.api",
    "awesome_agent.application",
    "awesome_agent.persistence",
    "awesome_agent.runtime",
    "awesome_agent.settings",
    "awesome_agent.storage",
    "fastapi",
    "langgraph",
    "sqlalchemy",
}
SDK_PROVIDER_FILES = {"deepseek.py", "errors.py", "kimi.py"}
REMOVED_PROVIDER_FILES = {"base.py", "openai.py", "routing.py"}
FORBIDDEN_TARGET_MARKERS = {
    "openrouter",
    "modelroute",
    "role_model",
    "role routing",
    "subprocess",
    "model_worker",
}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.add(node.module)
    return result


def _violations(paths: tuple[Path, ...], forbidden: set[str]) -> dict[str, list[str]]:
    result = {
        path.as_posix(): sorted(
            imported
            for imported in _imports(path)
            if any(
                imported == denied or imported.startswith(f"{denied}.")
                for denied in forbidden
            )
        )
        for path in paths
    }
    return {path: imports for path, imports in result.items() if imports}


def test_target_modeling_is_provider_and_platform_neutral() -> None:
    root = Path("src/awesome_agent/modeling")
    target = tuple(
        path for path in root.glob("*.py") if path.name not in ISOLATED_LEGACY_MODELING
    )

    assert _violations(target, FORBIDDEN_MODELING_IMPORTS) == {}


def test_provider_sdk_is_confined_to_concrete_adapters() -> None:
    root = Path("src/awesome_agent/providers")
    target = tuple(root.glob("*.py"))

    assert _violations(target, FORBIDDEN_PROVIDER_IMPORTS) == {}
    sdk_importers = {
        path.name
        for path in target
        if any(
            imported == "openai" or imported.startswith("openai.")
            for imported in _imports(path)
        )
    }
    assert sdk_importers <= SDK_PROVIDER_FILES


def test_obsolete_provider_modules_and_target_markers_are_absent() -> None:
    roots = (Path("src/awesome_agent/modeling"), Path("src/awesome_agent/providers"))
    target = tuple(
        path
        for root in roots
        for path in root.glob("*.py")
        if path.name not in ISOLATED_LEGACY_MODELING
    )
    content = "\n".join(path.read_text(encoding="utf-8").casefold() for path in target)

    assert not (
        {path.name for path in Path("src/awesome_agent/providers").glob("*.py")}
        & REMOVED_PROVIDER_FILES
    )
    assert not {marker for marker in FORBIDDEN_TARGET_MARKERS if marker in content}


def test_legacy_process_modules_are_not_exported_or_imported_by_target() -> None:
    modeling_root = Path("src/awesome_agent/modeling")
    target = tuple(
        path
        for path in modeling_root.glob("*.py")
        if path.name not in ISOLATED_LEGACY_MODELING
    )
    forbidden_modules = {
        f"awesome_agent.modeling.{Path(name).stem}" for name in ISOLATED_LEGACY_MODELING
    }

    assert _violations(target, forbidden_modules) == {}


def test_adapters_do_not_accept_custom_base_url_parameters() -> None:
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
