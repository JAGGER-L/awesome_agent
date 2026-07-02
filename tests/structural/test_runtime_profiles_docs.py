from pathlib import Path


def test_runtime_profiles_design_doc_exists_and_names_defaults() -> None:
    doc = Path("docs/design-docs/runtime-profiles-and-startup.md")
    text = doc.read_text(encoding="utf-8")

    assert "Docker API profile" in text
    assert "Local API development profile" in text
    assert "Local CLI/TUI profile" in text
    assert "AIO Docker" in text
    assert "LocalSandbox" in text
    assert "make docker-init" in text
    assert "make docker-start" in text
    assert "make check" in text
    assert "make install" in text
    assert "make setup-sandbox" in text
    assert "make dev" in text
    assert "`awesome`" in text


def test_roadmap_contains_tasks_57_to_64() -> None:
    text = Path("docs/project-governance/runtime-roadmap.md").read_text(
        encoding="utf-8"
    )

    for task_number in range(57, 65):
        assert f"Task {task_number}" in text


def test_token_only_budget_language_is_preserved() -> None:
    text = Path("docs/project-governance/runtime-roadmap.md").read_text(
        encoding="utf-8"
    )
    normalized = " ".join(text.split())

    assert (
        "Monetary amount limits are intentionally outside the runtime kernel"
        in normalized
    )
    forbidden = ["USD", "currency", "billing limits", "money-based limits"]
    assert not any(term in text for term in forbidden)
