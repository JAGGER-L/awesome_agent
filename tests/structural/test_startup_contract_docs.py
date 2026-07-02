from __future__ import annotations

from pathlib import Path


def test_local_tui_is_documented_as_embedded_by_default() -> None:
    text = _normalized("docs/design-docs/runtime-profiles-and-startup.md")

    assert "embedded local runtime mode" in text
    assert "does not require an API server" in text
    assert "awesome --api-url <url>" in text


def test_readmes_document_local_tui_without_api_requirement() -> None:
    for path in ("README.md", "README.zh-CN.md"):
        text = _normalized(path)
        assert "embedded local runtime" in text
        assert "awesome --api-url" in text
        assert "make dev" in text
        assert "make docker-start" in text
        assert "Ordinary input is the main execution entry" in text


def test_run_command_is_not_documented_as_primary_entrypoint() -> None:
    text = _normalized("docs/project-governance/runtime-roadmap.md")

    assert "`/run` remains available only as" in text
    assert "not the required path for normal agent work" in text


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
        assert "fallback" in window or "debug" in window


def _normalized(path: str) -> str:
    return " ".join(Path(path).read_text(encoding="utf-8").split())
