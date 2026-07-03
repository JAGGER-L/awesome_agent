from __future__ import annotations

from pathlib import Path


def test_local_tui_is_documented_as_embedded_by_default() -> None:
    text = _normalized("docs/design-docs/runtime-profiles-and-startup.md")

    assert "embedded local runtime mode" in text
    assert "does not require an API server" in text
    assert "awesome --api-url <url>" in text


def test_readmes_document_local_tui_without_api_requirement() -> None:
    expected_turn_terms = {
        "README.md": "user message",
        "README.zh-CN.md": "用户消息",
    }
    for path, expected_turn_term in expected_turn_terms.items():
        text = _normalized(path)
        assert "embedded local runtime" in text
        assert "awesome --api-url" in text
        assert "make dev" in text
        assert "make docker-start" in text
        assert expected_turn_term in text


def test_readmes_document_user_message_product_entry_contract() -> None:
    english = _normalized("README.md")
    chinese = _normalized("README.zh-CN.md")

    assert "Run `awesome` from the project directory" in english
    assert "launch directory becomes the default thread context" in english
    assert "If it is a Git checkout, Runs inherit that repository" in english
    assert "workspace-only mode and still accepts user message turns" in english
    assert "Plain user messages are the only product execution creation path" in english
    assert "internal conversation Run with a Leader Agent" in english

    assert "从项目目录运行 `awesome`" in chinese
    assert "启动目录会成为默认 thread context" in chinese
    assert "Git checkout" in chinese
    assert "Runs 会继承该 repository" in chinese
    assert "workspace-only mode" in chinese
    assert "用户消息 turn" in chinese
    assert "唯一的产品执行创建路径" in chinese
    assert "内部 conversation Run" in chinese
    assert "Leader Agent" in chinese


def test_readmes_do_not_document_removed_product_run_entries() -> None:
    removed_cli_entry = "awesome-agent" + " run"
    removed_slash_table = "| `/" + "run` |"

    for path in ("README.md", "README.zh-CN.md"):
        text = Path(path).read_text(encoding="utf-8")
        assert removed_cli_entry not in text
        assert removed_slash_table not in text


def test_user_message_turn_is_documented_as_primary_entrypoint() -> None:
    text = _normalized("docs/project-governance/runtime-roadmap.md")

    assert (
        "user message input is the only product execution creation entry"
        in text.lower()
    )
    assert "`/" + "run` remains available only as" not in text


def test_start_command_is_documented_as_fallback() -> None:
    for path in (
        "README.md",
        "README.zh-CN.md",
        "docs/getting-started/quickstart.md",
        "docs/operations/README.md",
    ):
        text = _normalized(path)
        assert "awesome-agent start" in text
        start_index = text.index("awesome-agent start")
        window = text[max(0, start_index - 120) : start_index + 160].lower()
        assert (
            "fallback" in window
            or "debug" in window
            or "备用" in window
            or "调试" in window
        )


def _normalized(path: str) -> str:
    return " ".join(Path(path).read_text(encoding="utf-8").split())
