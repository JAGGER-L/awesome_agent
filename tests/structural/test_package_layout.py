from pathlib import Path

import awesome_agent


def test_package_is_imported_from_src_layout() -> None:
    package_path = Path(awesome_agent.__file__).resolve()

    assert "src" in package_path.parts
    assert package_path.parent.name == "awesome_agent"
