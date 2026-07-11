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

    assert ".codex/" in gitignore
    assert not any((ROOT / "docs" / "exec-plans").rglob("*.*"))
    assert (ROOT / "docs" / "development" / "repository-harness.md").is_file()
    assert (ROOT / "docs" / "architecture" / "runtime-kernel.md").is_file()
    assert "do not define the behavior" in agent_contract
    plan_rules = (ROOT / "docs" / "development" / "execution-plans.md").read_text(
        encoding="utf-8"
    )
    assert ".codex/" in plan_rules


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
    assert (ROOT / "scripts" / "generate_protocol_fixtures.py").is_file()
    assert any((ROOT / "protocol" / "fixtures").rglob("*.json"))

    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for ignored in {
        ".codex/",
        ".worktrees/",
        ".venv/",
        "__pycache__/",
        "*.py[cod]",
    }:
        assert ignored in gitignore
