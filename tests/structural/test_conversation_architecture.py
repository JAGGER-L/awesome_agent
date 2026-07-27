import ast
from pathlib import Path

CONVERSATION_MODULES = {
    "__init__.py",
    "export.py",
    "materialization.py",
    "models.py",
    "repository.py",
    "service.py",
    "titles.py",
}


def test_conversation_module_inventory_is_current() -> None:
    assert {
        path.name for path in Path("src/awesome_agent/conversation").glob("*.py")
    } == CONVERSATION_MODULES


def test_conversation_exposes_one_aggregate_repository_port() -> None:
    path = Path("src/awesome_agent/conversation/repository.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    protocols = {
        node.name
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and any(
            isinstance(base, ast.Name) and base.id == "Protocol" for base in node.bases
        )
    }

    assert protocols == {"ConversationStore"}
