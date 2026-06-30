from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

DOC_PATHS = [
    ROOT / "README.md",
    ROOT / "README.zh-CN.md",
    ROOT / "docs" / "QUALITY_SCORE.md",
    ROOT / "docs" / "product-specs" / "local-coding-agent.md",
    ROOT / "docs" / "design-docs" / "durable-execution.md",
    ROOT / "docs" / "design-docs" / "observability.md",
    ROOT / "docs" / "project-governance" / "runtime-roadmap.md",
    ROOT / "docs" / "project-governance" / "tech-debt-tracker.md",
]

SOURCE_PATHS = [
    *list((ROOT / "src" / "awesome_agent").rglob("*.py")),
    *list((ROOT / "migrations" / "versions").rglob("*.py")),
    ROOT / "docs" / "generated" / "db-schema.md",
]

BANNED_DOC_PHRASES = [
    "money cost budget",
    "money-cost budget",
    "money-cost budget enforcement",
    "cost budget",
    "cost budgeting",
    "spend budget",
    "spend ledger",
    "pricing configuration",
    "pricing source",
    "auditable spend",
]

BANNED_SOURCE_MARKERS = [
    "estimated" + "_cost" + "_usd",
    "max_cost",
    "cost_limit",
    "spend_limit",
    "pricing_config",
    "pricing_source",
    "CostBudget",
    "SpendBudget",
]


def test_docs_do_not_plan_amount_budgets() -> None:
    failures: list[str] = []
    for path in DOC_PATHS:
        content = path.read_text(encoding="utf-8").lower()
        for phrase in BANNED_DOC_PHRASES:
            if phrase in content:
                failures.append(f"{path.relative_to(ROOT)} contains {phrase!r}")

    assert not failures, "Amount-budget roadmap language remains:\n" + "\n".join(
        failures
    )


def test_source_schema_and_migrations_have_no_amount_budget_markers() -> None:
    failures: list[str] = []
    for path in SOURCE_PATHS:
        content = path.read_text(encoding="utf-8")
        for marker in BANNED_SOURCE_MARKERS:
            if marker in content:
                failures.append(f"{path.relative_to(ROOT)} contains {marker!r}")

    assert not failures, "Amount-budget source surface remains:\n" + "\n".join(failures)
