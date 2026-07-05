from pathlib import Path


def test_runtime_profiles_design_doc_exists_and_names_defaults() -> None:
    doc = Path("docs/architecture/product-surfaces.md")
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


def test_roadmap_is_product_direction_not_task_journal() -> None:
    text = Path("docs/governance/roadmap.md").read_text(encoding="utf-8")

    assert "## Product Thesis" in text
    assert "## Strategic Pillars" in text
    assert "## Now" in text
    assert "## Next" in text
    assert "## Later" in text
    assert "## Completed Milestones" in text
    assert "Task 57" not in text


def test_token_only_budget_language_is_preserved() -> None:
    text = Path("docs/governance/roadmap.md").read_text(encoding="utf-8")
    normalized = " ".join(text.split())

    assert (
        "Monetary amount limits are intentionally outside the runtime kernel"
        in normalized
    )
    forbidden = ["USD", "currency", "billing limits", "money-based limits"]
    assert not any(term in text for term in forbidden)
