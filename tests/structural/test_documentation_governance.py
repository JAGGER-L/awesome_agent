from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_documentation_entry_points_exist() -> None:
    for relative in [
        "docs/README.md",
        "docs/getting-started/quickstart.md",
        "docs/user-guide/README.md",
        "docs/operations/README.md",
        "docs/architecture/README.md",
        "docs/api/README.md",
        "docs/development/README.md",
        "docs/governance/documentation.md",
        "docs/governance/roadmap.md",
        "docs/governance/technical-debt.md",
        "docs/reference/README.md",
    ]:
        assert (ROOT / relative).is_file(), relative


def test_readme_links_to_docs_map() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "docs/README.md" in text
    assert "docs/getting-started/quickstart.md" in text
    assert "docs/user-guide/README.md" in text
    assert "docs/operations/README.md" in text


def test_agent_contract_mentions_repository_and_plan_maps() -> None:
    text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    for expected in [
        "## Documentation Map",
        "## Repository Map",
        ".codex/exec-plans/completed/",
        "src/awesome_agent/extensions/",
    ]:
        assert expected in text
