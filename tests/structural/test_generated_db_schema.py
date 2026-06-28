from pathlib import Path

from scripts.generate_db_docs import generate_schema_markdown


def test_generated_db_schema_matches_sqlalchemy_metadata() -> None:
    expected = generate_schema_markdown()
    current = Path("docs/generated/db-schema.md").read_text(encoding="utf-8")

    assert current == expected
