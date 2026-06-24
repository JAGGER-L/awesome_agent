from scripts.check_docs_sync import missing_documentation, required_docs_for


def test_provider_change_requires_specific_provider_documents() -> None:
    requirements = required_docs_for({"src/awesome_agent/providers/deepseek.py"})

    assert requirements["model providers"] == {
        "README.md",
        "README.zh-CN.md",
        "ARCHITECTURE.md",
        "docs/design-docs/agent-team-and-subagents.md",
    }


def test_unrelated_document_does_not_satisfy_persistence_change() -> None:
    missing = missing_documentation(
        {
            "src/awesome_agent/persistence/database.py",
            "docs/FRONTEND.md",
        }
    )

    assert missing["persistence or schema"] == {
        "ARCHITECTURE.md",
        "docs/RELIABILITY.md",
        "docs/generated/db-schema.md",
    }


def test_all_mapped_documents_satisfy_change() -> None:
    changed = {
        "src/awesome_agent/memory/external.py",
        "docs/design-docs/memory-architecture.md",
        "docs/SECURITY.md",
        "docs/RELIABILITY.md",
    }

    assert missing_documentation(changed) == {}
