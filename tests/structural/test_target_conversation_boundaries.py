from __future__ import annotations

import ast
from pathlib import Path

COMMON_FORBIDDEN_IMPORTS = {
    "awesome_agent.api",
    "awesome_agent.attachments",
    "awesome_agent.client",
    "awesome_agent.persistence",
    "awesome_agent.providers",
    "awesome_agent.runtime",
    "awesome_agent.sandbox",
    "awesome_agent.surfaces",
    "awesome_agent.tui",
    "fastapi",
    "openai",
    "psycopg",
    "sqlalchemy",
    "textual",
}

CONFIG_FORBIDDEN_IMPORTS = COMMON_FORBIDDEN_IMPORTS | {
    "awesome_agent.application",
    "awesome_agent.conversation",
    "awesome_agent.storage",
}

CONVERSATION_FORBIDDEN_IMPORTS = COMMON_FORBIDDEN_IMPORTS | {
    "awesome_agent.application",
    "awesome_agent.storage",
    "langgraph",
}

REMOVED_LEGACY_CONVERSATION_FILES = {
    "events.py",
    "intake.py",
    "runtime_turns.py",
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


def _violations(root: Path, forbidden: set[str]) -> dict[str, list[str]]:
    result = {
        path.as_posix(): sorted(
            imported
            for imported in _imports(path)
            if any(
                imported == denied or imported.startswith(f"{denied}.")
                for denied in forbidden
            )
        )
        for path in root.rglob("*.py")
    }
    return {path: imports for path, imports in result.items() if imports}


def test_target_config_is_independent_of_runtime_and_product_state() -> None:
    assert _violations(Path("src/awesome_agent/config"), CONFIG_FORBIDDEN_IMPORTS) == {}


def test_target_conversation_is_framework_free_and_storage_independent() -> None:
    assert (
        _violations(
            Path("src/awesome_agent/conversation"),
            CONVERSATION_FORBIDDEN_IMPORTS,
        )
        == {}
    )


def test_obsolete_runtime_conversation_projection_is_deleted() -> None:
    root = Path("src/awesome_agent/conversation")

    assert (
        not {path.name for path in root.glob("*.py")}
        & REMOVED_LEGACY_CONVERSATION_FILES
    )
