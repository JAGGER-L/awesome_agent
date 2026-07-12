from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REQUIRED_ROOT_FILES = {
    ".editorconfig",
    ".env.example",
    ".gitattributes",
    ".gitignore",
    ".python-version",
    "AGENTS.md",
    "ARCHITECTURE.md",
    "CLAUDE.md",
    "README.md",
    "README.zh-CN.md",
    "VERSION",
    "install.ps1",
    "install.sh",
    "pyproject.toml",
    "uv.lock",
}


def test_development_coordination_and_product_state_are_separate() -> None:
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    agent_contract = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    claude_contract = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")

    assert ".codex/" in gitignore
    assert not any((ROOT / "docs" / "exec-plans").rglob("*.*"))
    assert ".codex/exec-plans/" in agent_contract
    assert "AGENTS.md" in claude_contract
    assert "single source of truth" in claude_contract

    for path in (
        ROOT / "docs/development/testing.md",
        ROOT / "docs/development/release.md",
        ROOT / "docs/architecture/agent-core.md",
        ROOT / "docs/architecture/application-and-langgraph.md",
    ):
        assert path.is_file()


def test_repository_has_current_harness_inputs() -> None:
    root_files = {path.name for path in ROOT.iterdir() if path.is_file()}

    assert root_files >= REQUIRED_ROOT_FILES
    assert (ROOT / "scripts/generate_protocol_fixtures.py").is_file()
    assert (ROOT / "scripts/release/build_bundle.py").is_file()
    assert any((ROOT / "protocol/fixtures").rglob("*.json"))

    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for ignored in {
        ".codex/",
        ".worktrees/",
        ".venv/",
        "__pycache__/",
        "*.py[cod]",
    }:
        assert ignored in gitignore
