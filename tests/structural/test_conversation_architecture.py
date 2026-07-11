from pathlib import Path

CONVERSATION_MODULES = {
    "__init__.py",
    "models.py",
    "repository.py",
    "service.py",
}


def test_conversation_module_inventory_is_current() -> None:
    assert {
        path.name for path in Path("src/awesome_agent/conversation").glob("*.py")
    } == CONVERSATION_MODULES
