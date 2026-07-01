import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXTENSIONS = ROOT / "src" / "awesome_agent" / "extensions"


def test_extension_sources_do_not_use_reflection_loading() -> None:
    forbidden = ("importlib", "__import__", "eval(", "exec(")
    offenders: list[str] = []
    for path in EXTENSIONS.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if any(marker in text for marker in forbidden):
            offenders.append(str(path.relative_to(ROOT)))

    assert offenders == []


def test_extension_code_does_not_introduce_monetary_budget_fields() -> None:
    forbidden = re.compile(r"\b(cost|price|currency|usd|monetary|amount)\b")
    offenders: list[str] = []
    for path in EXTENSIONS.rglob("*.py"):
        lowered = path.read_text(encoding="utf-8").lower()
        if forbidden.search(lowered):
            offenders.append(str(path.relative_to(ROOT)))

    assert offenders == []


def test_external_extension_tool_names_are_namespaced() -> None:
    checked = [
        EXTENSIONS / "mcp" / "stdio.py",
        EXTENSIONS / "mcp" / "http.py",
        EXTENSIONS / "community.py",
    ]
    text = "\n".join(path.read_text(encoding="utf-8") for path in checked)

    assert "mcp.{self._config.id}." in text
    assert "community.{manifest.id}." in text


def test_runtime_does_not_import_extension_tool_handlers_directly() -> None:
    runtime_root = ROOT / "src" / "awesome_agent" / "runtime"
    forbidden = (
        "McpStdioToolHandler",
        "McpStreamableHttpToolHandler",
        "CommunitySubprocessJsonHandler",
        "register_mcp_stdio_tools",
        "register_mcp_streamable_http_tools",
        "register_community_tools",
    )
    offenders: list[str] = []
    for path in runtime_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if any(marker in text for marker in forbidden):
            offenders.append(str(path.relative_to(ROOT)))

    assert offenders == []
