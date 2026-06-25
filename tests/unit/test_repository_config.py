from pathlib import Path

from awesome_agent.repositories.config import LocalRepositoryConfigStore


def test_config_store_adds_and_removes_normalized_roots(tmp_path: Path) -> None:
    store = LocalRepositoryConfigStore(tmp_path / "config.toml")
    root = tmp_path / "projects"
    root.mkdir()

    added = store.add_root(root)
    loaded = store.load()

    assert added.allowed_roots == [root.resolve()]
    assert loaded.allowed_roots == [root.resolve()]
    assert "\\" not in store.path.read_text(encoding="utf-8")

    removed = store.remove_root(root)
    assert removed.allowed_roots == []
    assert store.load().allowed_roots == []
