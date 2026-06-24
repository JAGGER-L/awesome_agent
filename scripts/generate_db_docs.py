from pathlib import Path

from awesome_agent.persistence.models import Base


def main() -> None:
    lines = [
        "# Database Schema",
        "",
        "Generated from SQLAlchemy metadata.",
        "",
    ]
    for table in sorted(Base.metadata.tables.values(), key=lambda item: item.name):
        lines.extend(
            [
                f"## `{table.name}`",
                "",
                "| Column | Type | Nullable |",
                "| --- | --- | --- |",
            ]
        )
        for column in table.columns:
            lines.append(
                f"| `{column.name}` | `{column.type}` | "
                f"{'yes' if column.nullable else 'no'} |"
            )
        lines.append("")

    target = Path("docs/generated/db-schema.md")
    target.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
