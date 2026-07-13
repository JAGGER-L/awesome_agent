from pathlib import Path

import awesome_agent

ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = ROOT / "src" / "awesome_agent"
EXPECTED_PACKAGES = {
    "agent",
    "application",
    "config",
    "context",
    "conversation",
    "core",
    "development",
    "extensions",
    "memory",
    "modeling",
    "protocol",
    "providers",
    "safety",
    "storage",
}
EXPECTED_MODULES = {"__init__.py", "paths.py", "version.py", "py.typed"}


def test_package_is_imported_from_src_layout() -> None:
    package_path = Path(awesome_agent.__file__).resolve()

    assert "src" in package_path.parts
    assert package_path.parent.name == "awesome_agent"


def test_python_package_matches_final_inventory() -> None:
    packages = {
        path.name
        for path in PACKAGE_ROOT.iterdir()
        if path.is_dir() and path.name != "__pycache__"
    }
    modules = {
        path.name
        for path in PACKAGE_ROOT.iterdir()
        if path.is_file() and path.name != "__pycache__"
    }

    assert packages == EXPECTED_PACKAGES
    assert modules == EXPECTED_MODULES
