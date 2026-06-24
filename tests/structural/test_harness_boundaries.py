from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_development_and_runtime_agent_state_are_separate() -> None:
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    agent_contract = (ROOT / "AGENTS.md").read_text(encoding="utf-8")

    assert ".codex/" in gitignore
    assert not any((ROOT / "docs" / "exec-plans").rglob("*.*"))
    assert (ROOT / ".agents" / "README.md").is_file()
    assert (ROOT / "docs" / "engineering" / "engineering-harness.md").is_file()
    assert (ROOT / "docs" / "design-docs" / "runtime-agent-harness.md").is_file()
    assert "do not define the behavior" in agent_contract
    plan_rules = (ROOT / "docs" / "engineering" / "execution-plans.md").read_text(
        encoding="utf-8"
    )
    assert ".codex/" in plan_rules
