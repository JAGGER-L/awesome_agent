from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class DemoParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.external_assets: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "script" and values.get("src"):
            self.external_assets.append(values["src"] or "")
        href = values.get("href") or ""
        if tag == "link" and href.startswith(("http://", "https://")):
            self.external_assets.append(href)


def test_frontend_demo_is_standalone_and_covers_runtime_views() -> None:
    html = (ROOT / "demo" / "index.html").read_text(encoding="utf-8")
    parser = DemoParser()
    parser.feed(html)

    assert parser.external_assets == []
    for contract in [
        "Agent Team",
        "动态任务",
        "实时追踪",
        "需要你的批准",
        "Verifier",
        "Subagent",
        "deepseek-v4-pro",
        "deepseek-v4-flash",
    ]:
        assert contract in html
