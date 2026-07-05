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
        "## Project Architecture",
        "### Documentation Map",
        "### Repository Map",
        ".codex/exec-plans/completed/",
        "src/awesome_agent/extensions/",
    ]:
        assert expected in text


def test_docs_do_not_contain_moved_redirect_stubs() -> None:
    offenders: list[str] = []
    for path in (ROOT / "docs").rglob("*.md"):
        text = path.read_text(encoding="utf-8").strip()
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if lines and lines[0] == "# Moved":
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_legacy_documentation_redirect_directories_are_removed() -> None:
    removed_parts = [
        ("design", "docs"),
        ("project", "governance"),
        ("generated",),
        ("references",),
        ("engineering",),
    ]
    removed = [ROOT / "docs" / "-".join(parts) for parts in removed_parts]
    assert [path.as_posix() for path in removed if path.exists()] == []
