from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OBSOLETE_ROOT_ASSETS = {
    "Makefile",
    "awesome-agent.yaml",
    "scripts/bootstrap.ps1",
    "scripts/check.ps1",
    "scripts/quickstart.ps1",
    "scripts/test.ps1",
    "scripts/make",
    "skills",
    ".agents/README.md",
}


def test_development_and_runtime_agent_state_are_separate() -> None:
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    agent_contract = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    claude_contract = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")

    assert ".codex/" in gitignore
    assert not any((ROOT / "docs" / "exec-plans").rglob("*.*"))
    assert ".codex/exec-plans/" in agent_contract
    development = " ".join(
        (ROOT / "docs/development/README.md").read_text(encoding="utf-8").split()
    )
    assert "never define Awesome runtime behavior" in development
    assert "AGENTS.md" in claude_contract
    assert "single source of truth" in claude_contract

    for path in (
        ROOT / "docs/development/testing.md",
        ROOT / "docs/development/release.md",
        ROOT / "docs/architecture/agent-core.md",
        ROOT / "docs/architecture/runtime-and-langgraph.md",
    ):
        assert path.is_file()


def test_repository_contains_only_target_harness_assets() -> None:
    assert not {
        path
        for relative in OBSOLETE_ROOT_ASSETS
        if (
            (path := ROOT / relative).is_file()
            or (path.is_dir() and any(item.is_file() for item in path.rglob("*")))
        )
    }
    assert (ROOT / ".env.example").is_file()
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
